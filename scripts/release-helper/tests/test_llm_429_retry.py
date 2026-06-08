"""Tests for 429 rate-limit retry logic in llm_classifier.

Covers:
- 429 is retried up to max_429_retries times (not counted against non-429 budget)
- retry_after from LlmTransientError is respected (wait >= retry_after)
- When max_429_retries exhausted → fallback unclear
- max_429_retries=0 → immediate fallback on first 429
- Non-429 transient errors still retry exactly once
- LlmTransientError.is_rate_limit and retry_after attributes
- _parse_retry_after helper: integer seconds, missing header
"""

from __future__ import annotations

import asyncio
import time
from io import BytesIO
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from changelog_tool.llm.base import LlmClient, LlmRequest, LlmResponse
from changelog_tool.llm.errors import LlmTransientError, LlmResponseError
from changelog_tool.llm_classifier import run_llm_classification
from changelog_tool.models import (
    AutoClassification,
    AutoClassificationReason,
    Commit,
    Confidence,
    LlmCategory,
    SizeBucket,
)

SHA1 = "a" * 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(sha: str) -> Commit:
    short = sha[:8]
    ac = AutoClassification(
        send_to_llm=True,
        reason=AutoClassificationReason.REQUIRES_LLM_ANALYSIS,
        category=None,
        candidate_for_changelog=None,
        changelog_line=None,
        confidence=None,
    )
    return Commit(
        sha=sha,
        short_sha=short,
        is_merge_commit=False,
        author_name="Alice",
        author_email="alice@example.com",
        co_authors=[],
        subject=f"Add feature {short}",
        body="",
        message=f"Add feature {short}",
        commit_url=f"https://github.com/org/repo/commit/{sha}",
        changed_files=["src/main.cpp"],
        insertions=300,
        deletions=0,
        files_count=1,
        size_score=300,
        size_bucket=SizeBucket.LARGE,
        auto_classification=ac,
    )


def _make_success_response(sha_list: List[str]) -> str:
    import json
    items = [
        {
            "sha": sha,
            "category": "feature",
            "candidate_for_changelog": True,
            "changelog_line": f"Added {sha[:8]}.",
            "comment": None,
            "confidence": "high",
        }
        for sha in sha_list
    ]
    return json.dumps({
        "items": items,
        "stats": {"input_count": len(sha_list), "output_count": len(items)},
    })


def _run(
    commits: List[Commit],
    client: LlmClient,
    *,
    max_rps: float = 0.0,
    max_429_retries: int = 3,
    workdir: str,
) -> any:
    import os
    state_path = os.path.join(workdir, "04_llm_classification_state.json")
    classified_path = os.path.join(workdir, "04_llm_classified.jsonl")
    return run_llm_classification(
        commits=commits,
        client=client,
        state_path=state_path,
        classified_path=classified_path,
        batch_size=20,
        max_prompt_chars=50000,
        from_ref="v1.0",
        to_ref="HEAD",
        max_rps=max_rps,
        max_429_retries=max_429_retries,
    )


# ---------------------------------------------------------------------------
# LlmTransientError attributes
# ---------------------------------------------------------------------------


class TestLlmTransientErrorAttributes:
    def test_is_rate_limit_default_false(self):
        exc = LlmTransientError("timeout")
        assert exc.is_rate_limit is False
        assert exc.retry_after is None

    def test_is_rate_limit_true(self):
        exc = LlmTransientError("429", is_rate_limit=True, retry_after=30.0)
        assert exc.is_rate_limit is True
        assert exc.retry_after == 30.0

    def test_retry_after_none_when_not_set(self):
        exc = LlmTransientError("429", is_rate_limit=True)
        assert exc.retry_after is None


# ---------------------------------------------------------------------------
# 429 retry behavior
# ---------------------------------------------------------------------------


class Test429Retry:
    def test_429_retried_then_succeeds(self, tmp_path):
        """429 on first attempt, success on second."""
        attempt_holder = [0]

        class FailOnceThen429ThenSucceed(LlmClient):
            async def async_complete(self, request):
                attempt_holder[0] += 1
                if attempt_holder[0] == 1:
                    raise LlmTransientError(
                        "429", is_rate_limit=True, retry_after=0.01
                    )
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(
                    text=_make_success_response(sha_list), backend="test"
                )

        result = _run(
            [_make_commit(SHA1)],
            FailOnceThen429ThenSucceed(),
            max_429_retries=3,
            workdir=str(tmp_path),
        )
        assert result.classifications[SHA1].category == LlmCategory.FEATURE
        assert attempt_holder[0] == 2

    def test_429_exhausted_falls_back_to_unclear(self, tmp_path):
        """All attempts return 429 → fallback unclear after max_429_retries."""
        call_count = [0]

        class Always429(LlmClient):
            async def async_complete(self, request):
                call_count[0] += 1
                raise LlmTransientError(
                    "429", is_rate_limit=True, retry_after=0.01
                )

        result = _run(
            [_make_commit(SHA1)],
            Always429(),
            max_429_retries=2,
            workdir=str(tmp_path),
        )
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        assert result.batches_failed == 1
        # 1 initial + 2 retries = 3 total calls
        assert call_count[0] == 3

    def test_max_429_retries_zero_immediate_fallback(self, tmp_path):
        """max_429_retries=0 → fallback on first 429, no retries."""
        call_count = [0]

        class Always429(LlmClient):
            async def async_complete(self, request):
                call_count[0] += 1
                raise LlmTransientError(
                    "429", is_rate_limit=True, retry_after=0.01
                )

        result = _run(
            [_make_commit(SHA1)],
            Always429(),
            max_429_retries=0,
            workdir=str(tmp_path),
        )
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        assert call_count[0] == 1

    def test_429_does_not_count_against_non_429_retry_budget(self, tmp_path):
        """After a 429 retry, a non-429 error still gets its own one retry."""
        attempt_holder = [0]

        class Mixed429ThenTransient(LlmClient):
            async def async_complete(self, request):
                attempt_holder[0] += 1
                if attempt_holder[0] == 1:
                    raise LlmTransientError(
                        "429", is_rate_limit=True, retry_after=0.01
                    )
                if attempt_holder[0] == 2:
                    raise LlmTransientError("timeout")  # non-429
                # attempt 3: success
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(
                    text=_make_success_response(sha_list), backend="test"
                )

        result = _run(
            [_make_commit(SHA1)],
            Mixed429ThenTransient(),
            max_429_retries=3,
            workdir=str(tmp_path),
        )
        assert result.classifications[SHA1].category == LlmCategory.FEATURE
        assert attempt_holder[0] == 3

    def test_retry_after_wait_is_respected(self, tmp_path):
        """When retry_after=0.1, the retry waits at least 0.1s."""
        times = []

        class RateLimitedBackend(LlmClient):
            async def async_complete(self, request):
                times.append(time.monotonic())
                if len(times) == 1:
                    raise LlmTransientError(
                        "429", is_rate_limit=True, retry_after=0.1
                    )
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(
                    text=_make_success_response(sha_list), backend="test"
                )

        _run(
            [_make_commit(SHA1)],
            RateLimitedBackend(),
            max_429_retries=3,
            workdir=str(tmp_path),
        )
        assert len(times) == 2
        gap = times[1] - times[0]
        assert gap >= 0.09, f"Expected wait >= 0.1s, got {gap:.3f}s"

    def test_rps_wait_used_when_no_retry_after(self, tmp_path):
        """When retry_after=None and max_rps=5, wait at least 1/5=0.2s."""
        times = []

        class RateLimitedNoHeader(LlmClient):
            async def async_complete(self, request):
                times.append(time.monotonic())
                if len(times) == 1:
                    raise LlmTransientError(
                        "429", is_rate_limit=True, retry_after=None
                    )
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(
                    text=_make_success_response(sha_list), backend="test"
                )

        _run(
            [_make_commit(SHA1)],
            RateLimitedNoHeader(),
            max_rps=5.0,  # 1/5 = 0.2s minimum wait
            max_429_retries=3,
            workdir=str(tmp_path),
        )
        assert len(times) == 2
        gap = times[1] - times[0]
        assert gap >= 0.18, f"Expected wait >= 0.2s, got {gap:.3f}s"


# ---------------------------------------------------------------------------
# Non-429 transient errors still retry once
# ---------------------------------------------------------------------------


class TestNon429RetryOnce:
    def test_non_429_retried_once_then_fallback(self, tmp_path):
        """Non-429 LlmTransientError: retry once, then fallback unclear."""
        call_count = [0]

        class AlwaysTimeout(LlmClient):
            async def async_complete(self, request):
                call_count[0] += 1
                raise LlmTransientError("timeout")

        result = _run(
            [_make_commit(SHA1)],
            AlwaysTimeout(),
            max_429_retries=5,
            workdir=str(tmp_path),
        )
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        assert call_count[0] == 2  # exactly 2: initial + 1 retry

    def test_non_429_success_on_second_attempt(self, tmp_path):
        """Non-429 error on first attempt, success on second."""
        attempt_holder = [0]

        class FailOnceThenSucceed(LlmClient):
            async def async_complete(self, request):
                attempt_holder[0] += 1
                if attempt_holder[0] == 1:
                    raise LlmTransientError("timeout")
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(
                    text=_make_success_response(sha_list), backend="test"
                )

        result = _run(
            [_make_commit(SHA1)],
            FailOnceThenSucceed(),
            max_429_retries=5,
            workdir=str(tmp_path),
        )
        assert result.classifications[SHA1].category == LlmCategory.FEATURE
        assert attempt_holder[0] == 2


# ---------------------------------------------------------------------------
# _parse_retry_after helper
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    def test_integer_seconds(self):
        from changelog_tool.llm.backends.http_api import _parse_retry_after
        import urllib.error
        exc = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests",
            hdrs=MagicMock(**{"get.return_value": "30"}),
            fp=BytesIO(b""),
        )
        result = _parse_retry_after(exc)
        assert result == 30.0

    def test_float_seconds(self):
        from changelog_tool.llm.backends.http_api import _parse_retry_after
        import urllib.error
        exc = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests",
            hdrs=MagicMock(**{"get.return_value": "1.5"}),
            fp=BytesIO(b""),
        )
        result = _parse_retry_after(exc)
        assert result == 1.5

    def test_missing_header_returns_none(self):
        from changelog_tool.llm.backends.http_api import _parse_retry_after
        import urllib.error
        exc = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests",
            hdrs=MagicMock(**{"get.return_value": None}),
            fp=BytesIO(b""),
        )
        result = _parse_retry_after(exc)
        assert result is None

    def test_none_headers_returns_none(self):
        from changelog_tool.llm.backends.http_api import _parse_retry_after
        import urllib.error
        exc = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests",
            hdrs=None,
            fp=BytesIO(b""),
        )
        result = _parse_retry_after(exc)
        assert result is None

    def test_http_backend_429_sets_is_rate_limit(self):
        """HttpApiBackend raises LlmTransientError with is_rate_limit=True on 429."""
        import urllib.error
        from unittest.mock import patch
        from changelog_tool.llm.backends.http_api import HttpApiBackend
        from changelog_tool.llm.base import LlmRequest

        exc = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests",
            hdrs=MagicMock(**{"get.return_value": "60"}),
            fp=BytesIO(b"rate limited"),
        )
        backend = HttpApiBackend(
            model="gpt-4o-mini",
            api_key="test-key",
            base_url="https://api.example.com/v1",
        )
        req = LlmRequest(system="sys", user="user")
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(LlmTransientError) as exc_info:
                backend.complete(req)
        err = exc_info.value
        assert err.is_rate_limit is True
        assert err.retry_after == 60.0
