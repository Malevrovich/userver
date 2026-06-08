"""Tests for async/parallel/rate-limit behavior in llm_classifier.

Covers:
- max_concurrency: multiple batches run in parallel (semaphore respected)
- max_rps: rate limiter delays requests appropriately
- async_complete() is called (not sync complete())
- Concurrent batches all complete and results are merged correctly
- Token-bucket: unlimited (max_rps=0) skips waiting
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import pytest

from changelog_tool.llm.base import LlmClient, LlmRequest, LlmResponse
from changelog_tool.llm.errors import LlmTransientError
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
SHA2 = "b" * 40
SHA3 = "c" * 40
SHA4 = "d" * 40
SHA5 = "e" * 40
SHA6 = "f" * 40


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


def _make_response(sha_list: List[str]) -> str:
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
    batch_size: int = 1,
    max_rps: float = 0.0,
    max_concurrency: int = 1,
    workdir: str,
) -> Any:
    import os
    state_path = os.path.join(workdir, "04_llm_classification_state.json")
    classified_path = os.path.join(workdir, "04_llm_classified.jsonl")
    return run_llm_classification(
        commits=commits,
        client=client,
        state_path=state_path,
        classified_path=classified_path,
        batch_size=batch_size,
        max_prompt_chars=50000,
        from_ref="v1.0",
        to_ref="HEAD",
        max_rps=max_rps,
        max_concurrency=max_concurrency,
    )


# ---------------------------------------------------------------------------
# Async complete() is used
# ---------------------------------------------------------------------------


class TestAsyncCompleteIsUsed:
    def test_async_complete_called_not_sync(self, tmp_path):
        """The classifier must call async_complete(), not complete()."""
        sync_calls = [0]
        async_calls = [0]

        class TrackingBackend(LlmClient):
            def complete(self, request):
                sync_calls[0] += 1
                raise AssertionError("sync complete() should not be called directly")

            async def async_complete(self, request):
                async_calls[0] += 1
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(text=_make_response(sha_list), backend="tracking")

        result = _run(
            [_make_commit(SHA1)],
            TrackingBackend(),
            workdir=str(tmp_path),
        )
        assert async_calls[0] >= 1
        assert sync_calls[0] == 0
        assert SHA1 in result.classifications


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_multiple_batches_run_in_parallel(self, tmp_path):
        """With max_concurrency=3 and 3 batches, all should run concurrently."""
        active_at_same_time = [0]
        max_active = [0]
        lock = asyncio.Lock()

        class SlowBackend(LlmClient):
            async def async_complete(self, request):
                nonlocal active_at_same_time, max_active
                # Track concurrency without asyncio.Lock (use list for thread safety).
                active_at_same_time[0] += 1
                if active_at_same_time[0] > max_active[0]:
                    max_active[0] = active_at_same_time[0]
                await asyncio.sleep(0.05)  # simulate network latency
                active_at_same_time[0] -= 1
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(text=_make_response(sha_list), backend="slow")

        commits = [_make_commit(sha) for sha in [SHA1, SHA2, SHA3]]
        result = _run(
            commits,
            SlowBackend(),
            batch_size=1,
            max_concurrency=3,
            workdir=str(tmp_path),
        )
        assert len(result.classifications) == 3
        # With concurrency=3, all 3 batches should have overlapped.
        assert max_active[0] > 1, (
            f"Expected concurrent execution, but max_active={max_active[0]}"
        )

    def test_concurrency_1_runs_sequentially(self, tmp_path):
        """With max_concurrency=1, batches run one at a time."""
        active_at_same_time = [0]
        max_active = [0]

        class SlowBackend(LlmClient):
            async def async_complete(self, request):
                active_at_same_time[0] += 1
                if active_at_same_time[0] > max_active[0]:
                    max_active[0] = active_at_same_time[0]
                await asyncio.sleep(0.02)
                active_at_same_time[0] -= 1
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(text=_make_response(sha_list), backend="slow")

        commits = [_make_commit(sha) for sha in [SHA1, SHA2, SHA3]]
        result = _run(
            commits,
            SlowBackend(),
            batch_size=1,
            max_concurrency=1,
            workdir=str(tmp_path),
        )
        assert len(result.classifications) == 3
        assert max_active[0] == 1, (
            f"Expected sequential execution, but max_active={max_active[0]}"
        )

    def test_all_results_present_with_high_concurrency(self, tmp_path):
        """All 6 commits classified correctly with concurrency=6."""
        class FastBackend(LlmClient):
            async def async_complete(self, request):
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(text=_make_response(sha_list), backend="fast")

        shas = [SHA1, SHA2, SHA3, SHA4, SHA5, SHA6]
        commits = [_make_commit(sha) for sha in shas]
        result = _run(
            commits,
            FastBackend(),
            batch_size=1,
            max_concurrency=6,
            workdir=str(tmp_path),
        )
        assert len(result.classifications) == 6
        for sha in shas:
            assert sha in result.classifications
            assert result.classifications[sha].category == LlmCategory.FEATURE


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_unlimited_rps_does_not_delay(self, tmp_path):
        """max_rps=0 (unlimited) should complete quickly."""
        class FastBackend(LlmClient):
            async def async_complete(self, request):
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(text=_make_response(sha_list), backend="fast")

        commits = [_make_commit(sha) for sha in [SHA1, SHA2, SHA3]]
        start = time.monotonic()
        result = _run(
            commits,
            FastBackend(),
            batch_size=1,
            max_rps=0.0,
            max_concurrency=3,
            workdir=str(tmp_path),
        )
        elapsed = time.monotonic() - start
        assert len(result.classifications) == 3
        # Should complete in well under 1 second with no rate limiting.
        assert elapsed < 1.0, f"Unlimited RPS took too long: {elapsed:.2f}s"

    def test_rate_limit_slows_requests(self, tmp_path):
        """max_rps=5 with 6 sequential requests should take at least ~1 second."""
        class FastBackend(LlmClient):
            async def async_complete(self, request):
                sha_list = request.metadata.get("sha_list", [])
                return LlmResponse(text=_make_response(sha_list), backend="fast")

        shas = [SHA1, SHA2, SHA3, SHA4, SHA5, SHA6]
        commits = [_make_commit(sha) for sha in shas]
        start = time.monotonic()
        result = _run(
            commits,
            FastBackend(),
            batch_size=1,
            max_rps=5.0,       # 5 RPS → 6 requests should take ≥ 1s
            max_concurrency=1,  # sequential to make timing predictable
            workdir=str(tmp_path),
        )
        elapsed = time.monotonic() - start
        assert len(result.classifications) == 6
        # 6 requests at 5 RPS → at least 1 second of waiting.
        assert elapsed >= 0.8, (
            f"Rate limiting did not slow requests enough: {elapsed:.2f}s"
        )


# ---------------------------------------------------------------------------
# Config fields parsed correctly
# ---------------------------------------------------------------------------


class TestConfigFields:
    def test_max_rps_default_is_zero(self):
        from changelog_tool.config import LlmConfig
        cfg = LlmConfig()
        assert cfg.max_rps == 0.0

    def test_max_concurrency_default_is_one(self):
        from changelog_tool.config import LlmConfig
        cfg = LlmConfig()
        assert cfg.max_concurrency == 1

    def test_max_rps_parsed_from_yaml(self, tmp_path):
        import yaml
        from changelog_tool.config import load_config
        cfg_path = tmp_path / "changelog.yaml"
        cfg_path.write_text("""
repo:
  owner: test
  name: test
  github_url: https://github.com/test/test
range:
  from: v1.0
llm:
  batch_size: 10
  max_prompt_chars: 10000
  max_rps: 3.5
  max_concurrency: 8
core_team:
  email_regexes: []
output:
  workdir: .changelog
""")
        cfg = load_config(str(cfg_path))
        assert cfg.llm.max_rps == 3.5
        assert cfg.llm.max_concurrency == 8

    def test_max_rps_zero_is_valid(self, tmp_path):
        import yaml
        from changelog_tool.config import load_config
        cfg_path = tmp_path / "changelog.yaml"
        cfg_path.write_text("""
repo:
  owner: test
  name: test
  github_url: https://github.com/test/test
range:
  from: v1.0
llm:
  batch_size: 10
  max_prompt_chars: 10000
  max_rps: 0
core_team:
  email_regexes: []
output:
  workdir: .changelog
""")
        cfg = load_config(str(cfg_path))
        assert cfg.llm.max_rps == 0.0

    def test_negative_max_rps_raises(self, tmp_path):
        from changelog_tool.config import load_config, ConfigError
        cfg_path = tmp_path / "changelog.yaml"
        cfg_path.write_text("""
repo:
  owner: test
  name: test
  github_url: https://github.com/test/test
range:
  from: v1.0
llm:
  batch_size: 10
  max_prompt_chars: 10000
  max_rps: -1
core_team:
  email_regexes: []
output:
  workdir: .changelog
""")
        with pytest.raises(ConfigError, match="max_rps"):
            load_config(str(cfg_path))
