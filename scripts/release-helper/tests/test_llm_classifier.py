"""Tests for changelog_tool.llm_classifier (offline, no network).

Covers:
- Basic classification of send_to_llm commits
- Commits with send_to_llm=False are ignored
- Oversized single commit → fallback unclear + attention
- Batch splitting by batch_size and max_prompt_chars
- State file written after each batch
- Resumability: already-done batches are skipped
- Retry once on LlmTransientError, then fallback unclear
- Retry once on LlmResponseError (invalid JSON), then fallback unclear
- Permanent LlmError → fallback unclear (no retry)
- 04_llm_classified.jsonl written correctly
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from changelog_tool.llm.backends.fake import FakeBackend
from changelog_tool.llm.base import LlmClient, LlmRequest, LlmResponse
from changelog_tool.llm.errors import LlmError, LlmResponseError, LlmTransientError
from changelog_tool.llm_classifier import (
    ATTENTION_TOO_LARGE,
    ClassificationResult,
    run_llm_classification,
)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(sha: str, send_to_llm: bool = True, size_score: int = 300) -> Commit:
    short = sha[:8]
    ac = AutoClassification(
        send_to_llm=send_to_llm,
        reason=(
            AutoClassificationReason.REQUIRES_LLM_ANALYSIS
            if send_to_llm
            else AutoClassificationReason.SMALL_COMMIT
        ),
        category=None if send_to_llm else LlmCategory.SMALL,
        candidate_for_changelog=None if send_to_llm else False,
        changelog_line=None,
        confidence=None if send_to_llm else Confidence.HIGH,
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
        insertions=size_score,
        deletions=0,
        files_count=1,
        size_score=size_score,
        size_bucket=SizeBucket.LARGE,
        auto_classification=ac,
    )


def _run(
    commits: List[Commit],
    client: LlmClient,
    *,
    batch_size: int = 20,
    max_prompt_chars: int = 50000,
    workdir: Optional[str] = None,
) -> ClassificationResult:
    """Helper: run classification in a temp dir."""
    if workdir is None:
        raise ValueError("workdir required")
    state_path = os.path.join(workdir, "04_llm_classification_state.json")
    classified_path = os.path.join(workdir, "04_llm_classified.jsonl")
    return run_llm_classification(
        commits=commits,
        client=client,
        state_path=state_path,
        classified_path=classified_path,
        batch_size=batch_size,
        max_prompt_chars=max_prompt_chars,
        from_ref="v1.0",
        to_ref="HEAD",
    )


# ---------------------------------------------------------------------------
# Basic classification
# ---------------------------------------------------------------------------


class TestBasicClassification:
    def test_single_commit_classified(self, tmp_path):
        backend = FakeBackend(responses={
            SHA1: {
                "category": "feature",
                "candidate_for_changelog": True,
                "changelog_line": "Added X.",
                "comment": "Good.",
                "confidence": "high",
            }
        })
        result = _run([_make_commit(SHA1)], backend, workdir=str(tmp_path))
        assert SHA1 in result.classifications
        assert result.classifications[SHA1].category == LlmCategory.FEATURE
        assert result.classifications[SHA1].candidate_for_changelog is True
        assert result.total_sent == 1

    def test_commits_with_send_to_llm_false_are_ignored(self, tmp_path):
        c1 = _make_commit(SHA1, send_to_llm=True)
        c2 = _make_commit(SHA2, send_to_llm=False)
        backend = FakeBackend()
        result = _run([c1, c2], backend, workdir=str(tmp_path))
        assert SHA1 in result.classifications
        assert SHA2 not in result.classifications
        assert result.total_sent == 1

    def test_no_commits_to_classify(self, tmp_path):
        c1 = _make_commit(SHA1, send_to_llm=False)
        backend = FakeBackend()
        result = _run([c1], backend, workdir=str(tmp_path))
        assert result.total_sent == 0
        assert result.classifications == {}

    def test_multiple_commits_classified(self, tmp_path):
        backend = FakeBackend()
        commits = [_make_commit(sha) for sha in [SHA1, SHA2, SHA3]]
        result = _run(commits, backend, workdir=str(tmp_path))
        assert len(result.classifications) == 3
        for sha in [SHA1, SHA2, SHA3]:
            assert sha in result.classifications

    def test_classified_jsonl_written(self, tmp_path):
        backend = FakeBackend()
        _run([_make_commit(SHA1)], backend, workdir=str(tmp_path))
        classified_path = tmp_path / "04_llm_classified.jsonl"
        assert classified_path.exists()
        lines = [json.loads(l) for l in classified_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        assert lines[0]["sha"] == SHA1
        assert "llm_classification" in lines[0]

    def test_state_file_written(self, tmp_path):
        backend = FakeBackend()
        _run([_make_commit(SHA1)], backend, workdir=str(tmp_path))
        state_path = tmp_path / "04_llm_classification_state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["operation"] == "llm_classification"
        assert "batches" in state


# ---------------------------------------------------------------------------
# Oversized commit fallback (Spec §9.7, §14.10)
# ---------------------------------------------------------------------------


class TestOversizedCommit:
    def test_oversized_commit_gets_unclear_fallback(self, tmp_path):
        """A commit whose single-commit prompt exceeds max_prompt_chars → unclear."""
        backend = FakeBackend()
        commit = _make_commit(SHA1, size_score=999999)
        # Set max_prompt_chars very small so the commit is "oversized".
        result = _run([commit], backend, workdir=str(tmp_path), max_prompt_chars=10)
        assert SHA1 in result.classifications
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        assert result.oversized_commits == 1

    def test_oversized_commit_does_not_prevent_others(self, tmp_path):
        """Other commits in the same run are still classified normally.

        We make SHA1 oversized by giving it a very long subject that inflates
        the single-commit prompt beyond the limit, while SHA2 stays small.
        """
        backend = FakeBackend(responses={
            SHA2: {"category": "feature", "candidate_for_changelog": True,
                   "changelog_line": "X.", "comment": None, "confidence": "high"},
        })

        # Build a commit whose context is large (long subject + body).
        big_subject = "A" * 5000
        oversized = _make_commit(SHA1)
        oversized.subject = big_subject
        oversized.body = "B" * 5000
        oversized.message = big_subject

        normal = _make_commit(SHA2)

        # Limit: large enough for SHA2's small prompt, too small for SHA1's.
        # SHA2 prompt ≈ system_prompt (~1500 chars) + small context (~200 chars).
        # SHA1 prompt ≈ system_prompt + 10000+ chars → exceeds 3000.
        result = _run(
            [oversized, normal], backend, workdir=str(tmp_path), max_prompt_chars=3000
        )
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        assert result.classifications[SHA2].category == LlmCategory.FEATURE


# ---------------------------------------------------------------------------
# Batch splitting
# ---------------------------------------------------------------------------


class TestBatchSplitting:
    def test_batch_size_respected(self, tmp_path):
        """With batch_size=1, each commit gets its own batch."""
        backend = FakeBackend()
        commits = [_make_commit(sha) for sha in [SHA1, SHA2, SHA3]]
        result = _run(commits, backend, workdir=str(tmp_path), batch_size=1)
        assert len(result.classifications) == 3
        # 3 commits → 3 batches
        state = json.loads((tmp_path / "04_llm_classification_state.json").read_text())
        assert len(state["batches"]) == 3

    def test_all_commits_in_one_batch_by_default(self, tmp_path):
        backend = FakeBackend()
        commits = [_make_commit(sha) for sha in [SHA1, SHA2]]
        result = _run(commits, backend, workdir=str(tmp_path), batch_size=20)
        state = json.loads((tmp_path / "04_llm_classification_state.json").read_text())
        assert len(state["batches"]) == 1


# ---------------------------------------------------------------------------
# Resumability (Spec §9.9)
# ---------------------------------------------------------------------------


class TestResumability:
    def test_already_done_batch_is_skipped(self, tmp_path):
        """Pre-populate state with a done batch; verify it is not re-sent."""
        call_count_holder = [0]

        class CountingBackend(FakeBackend):
            def complete(self, request):
                call_count_holder[0] += 1
                return super().complete(request)

        backend = CountingBackend(responses={
            SHA1: {"category": "feature", "candidate_for_changelog": True,
                   "changelog_line": "X.", "comment": None, "confidence": "high"},
        })

        # First run.
        _run([_make_commit(SHA1)], backend, workdir=str(tmp_path))
        first_call_count = call_count_holder[0]

        # Second run — should skip the already-done batch.
        result = _run([_make_commit(SHA1)], backend, workdir=str(tmp_path))
        assert call_count_holder[0] == first_call_count  # no new calls
        assert result.batches_skipped_already_done == 1

    def test_results_preserved_on_resume(self, tmp_path):
        """Results from the first run are available after resume."""
        backend = FakeBackend(responses={
            SHA1: {"category": "improvement", "candidate_for_changelog": True,
                   "changelog_line": "Improved Y.", "comment": None, "confidence": "medium"},
        })
        _run([_make_commit(SHA1)], backend, workdir=str(tmp_path))

        # Second run with a different (empty) backend — results come from state.
        result = _run([_make_commit(SHA1)], FakeBackend(), workdir=str(tmp_path))
        assert result.classifications[SHA1].category == LlmCategory.IMPROVEMENT


# ---------------------------------------------------------------------------
# Retry and fallback (Spec §9.10)
# ---------------------------------------------------------------------------


class TestRetryAndFallback:
    def test_transient_error_retried_once_then_fallback(self, tmp_path):
        """LlmTransientError on both attempts → fallback unclear."""
        class AlwaysTransientBackend(LlmClient):
            def complete(self, request):
                raise LlmTransientError("timeout")

        result = _run([_make_commit(SHA1)], AlwaysTransientBackend(), workdir=str(tmp_path))
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        assert result.batches_failed == 1

    def test_transient_error_then_success(self, tmp_path):
        """LlmTransientError on first attempt, success on second."""
        attempt_holder = [0]
        fake = FakeBackend(responses={
            SHA1: {"category": "feature", "candidate_for_changelog": True,
                   "changelog_line": "X.", "comment": None, "confidence": "high"},
        })

        class FailOnceThenSucceed(LlmClient):
            def complete(self, request):
                attempt_holder[0] += 1
                if attempt_holder[0] == 1:
                    raise LlmTransientError("first attempt fails")
                return fake.complete(request)

        result = _run([_make_commit(SHA1)], FailOnceThenSucceed(), workdir=str(tmp_path))
        assert result.classifications[SHA1].category == LlmCategory.FEATURE
        assert result.batches_done == 1
        assert result.batches_failed == 0

    def test_response_error_retried_once_then_fallback(self, tmp_path):
        """LlmResponseError (invalid JSON) on both attempts → fallback unclear."""
        class AlwaysInvalidJsonBackend(LlmClient):
            def complete(self, request):
                return LlmResponse(text="not json at all", backend="test")

        result = _run([_make_commit(SHA1)], AlwaysInvalidJsonBackend(), workdir=str(tmp_path))
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        assert result.batches_failed == 1

    def test_permanent_error_no_retry(self, tmp_path):
        """Permanent LlmError → fallback unclear immediately (no retry)."""
        call_count = [0]

        class PermanentErrorBackend(LlmClient):
            def complete(self, request):
                call_count[0] += 1
                raise LlmError("bad API key")

        result = _run([_make_commit(SHA1)], PermanentErrorBackend(), workdir=str(tmp_path))
        assert result.classifications[SHA1].category == LlmCategory.UNCLEAR
        # Permanent error stops after 1 attempt (no retry).
        assert call_count[0] == 1

    def test_fallback_batch_marked_done_in_state(self, tmp_path):
        """Even failed batches are marked done (with fallback) so they are not retried."""
        class AlwaysTransientBackend(LlmClient):
            def complete(self, request):
                raise LlmTransientError("timeout")

        _run([_make_commit(SHA1)], AlwaysTransientBackend(), workdir=str(tmp_path))
        state = json.loads((tmp_path / "04_llm_classification_state.json").read_text())
        batch = list(state["batches"].values())[0]
        assert batch["status"] == "done"
        assert batch.get("fallback") is True
