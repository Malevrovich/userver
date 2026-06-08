"""Tests for changelog_tool.llm.context (offline, no real git).

Covers:
- commit_to_context: emits exactly the Spec §9.8 field set, no diff by default
- maybe_attach_diff: truncates to diff_max_chars, marks truncation, handles errors
"""

from __future__ import annotations

import pytest

from changelog_tool.llm.context import commit_to_context, maybe_attach_diff
from changelog_tool.models import Commit, CoAuthor, SizeBucket

SHA = "a" * 40


def _make_commit(**kwargs) -> Commit:
    defaults = dict(
        sha=SHA,
        short_sha=SHA[:8],
        is_merge_commit=False,
        author_name="Alice",
        author_email="alice@example.com",
        co_authors=[],
        subject="Add Redis pipelining",
        body="Implements pipeline support.",
        message="Add Redis pipelining\n\nImplements pipeline support.",
        commit_url=f"https://github.com/org/repo/commit/{SHA}",
        changed_files=["redis/pipeline.cpp", "redis/tests/pipeline_test.cpp"],
        insertions=200,
        deletions=10,
        files_count=2,
        size_score=210,
        size_bucket=SizeBucket.LARGE,
    )
    defaults.update(kwargs)
    return Commit(**defaults)


# ---------------------------------------------------------------------------
# commit_to_context
# ---------------------------------------------------------------------------


class TestCommitToContext:
    def test_contains_sha(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["sha"] == SHA

    def test_contains_subject(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["subject"] == "Add Redis pipelining"

    def test_contains_body(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["body"] == "Implements pipeline support."

    def test_contains_changed_files(self):
        ctx = commit_to_context(_make_commit())
        assert "redis/pipeline.cpp" in ctx["changed_files"]

    def test_contains_insertions(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["insertions"] == 200

    def test_contains_deletions(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["deletions"] == 10

    def test_contains_files_count(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["files_count"] == 2

    def test_contains_size_score(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["size_score"] == 210

    def test_contains_commit_url(self):
        ctx = commit_to_context(_make_commit())
        assert ctx["commit_url"].endswith(SHA)

    def test_no_diff_by_default(self):
        ctx = commit_to_context(_make_commit())
        assert "diff" not in ctx

    def test_spec_98_exact_field_set(self):
        """Exactly the §9.8 fields — no extras, no missing."""
        ctx = commit_to_context(_make_commit())
        expected_keys = {
            "sha", "subject", "body", "changed_files",
            "insertions", "deletions", "files_count", "size_score", "commit_url",
        }
        assert set(ctx.keys()) == expected_keys


# ---------------------------------------------------------------------------
# maybe_attach_diff
# ---------------------------------------------------------------------------


def _fake_git_runner(cmd, cwd):
    """Fake git runner that returns a fixed diff string."""
    return "diff --git a/redis/pipeline.cpp b/redis/pipeline.cpp\n+added line\n"


def _error_git_runner(cmd, cwd):
    raise RuntimeError("git show failed: not a git repo")


class TestMaybeAttachDiff:
    def test_attaches_diff(self):
        ctx = commit_to_context(_make_commit())
        maybe_attach_diff(ctx, _make_commit(), "/repo", 10000, git_runner=_fake_git_runner)
        assert "diff" in ctx
        assert "added line" in ctx["diff"]

    def test_truncates_to_max_chars(self):
        ctx = commit_to_context(_make_commit())
        maybe_attach_diff(ctx, _make_commit(), "/repo", 10, git_runner=_fake_git_runner)
        assert len(ctx["diff"]) > 10  # includes truncation marker
        assert "TRUNCATED" in ctx["diff"]

    def test_no_truncation_when_within_limit(self):
        ctx = commit_to_context(_make_commit())
        maybe_attach_diff(ctx, _make_commit(), "/repo", 100000, git_runner=_fake_git_runner)
        assert "TRUNCATED" not in ctx["diff"]

    def test_error_sets_diff_null_and_diff_error(self):
        ctx = commit_to_context(_make_commit())
        maybe_attach_diff(ctx, _make_commit(), "/repo", 10000, git_runner=_error_git_runner)
        assert ctx["diff"] is None
        assert "diff_error" in ctx
        assert "git show failed" in ctx["diff_error"]

    def test_returns_context_for_chaining(self):
        ctx = commit_to_context(_make_commit())
        result = maybe_attach_diff(ctx, _make_commit(), "/repo", 10000, git_runner=_fake_git_runner)
        assert result is ctx

    def test_uses_real_runner_by_default_signature(self):
        """Verify the default runner parameter is None (real runner used when not injected)."""
        import inspect
        from changelog_tool.llm.context import maybe_attach_diff as fn
        sig = inspect.signature(fn)
        assert sig.parameters["git_runner"].default is None
