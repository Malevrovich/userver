"""Tests for changelog_tool.classifier — heuristic pre-classification."""

from __future__ import annotations

from typing import List

import pytest

from changelog_tool.classifier import _classify, preclassify
from changelog_tool.models import (
    AutoClassificationReason,
    Commit,
    Confidence,
    LlmCategory,
    SizeBucket,
)

# Default thresholds matching spec defaults
SMALL = 50
BUGFIX = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(
    subject: str = "Add feature",
    size_score: int = 100,
    is_merge_commit: bool = False,
    sha: str = "a" * 40,
) -> Commit:
    return Commit(
        sha=sha,
        short_sha=sha[:8],
        is_merge_commit=is_merge_commit,
        author_name="Alice",
        author_email="alice@example.com",
        co_authors=[],
        subject=subject,
        body="",
        message=subject,
        commit_url=f"https://github.com/org/repo/commit/{sha}",
        changed_files=["src/x.cpp"],
        insertions=size_score,
        deletions=0,
        files_count=1,
        size_score=size_score,
        size_bucket=SizeBucket.MEDIUM,
    )


# ---------------------------------------------------------------------------
# Rule 1: merge commit (Spec §8.6)
# ---------------------------------------------------------------------------


class TestRule1MergeCommit:
    def test_merge_commit_not_sent_to_llm(self):
        commit = _make_commit(is_merge_commit=True)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is False

    def test_merge_commit_reason(self):
        commit = _make_commit(is_merge_commit=True)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason == AutoClassificationReason.MERGE_COMMIT

    def test_merge_commit_category_unclear(self):
        commit = _make_commit(is_merge_commit=True)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.category == LlmCategory.UNCLEAR

    def test_merge_commit_confidence_low(self):
        commit = _make_commit(is_merge_commit=True)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.confidence == Confidence.LOW

    def test_merge_commit_not_candidate(self):
        commit = _make_commit(is_merge_commit=True)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.candidate_for_changelog is False

    def test_merge_commit_wins_over_docs(self):
        """Rule 1 must win even if subject also matches docs."""
        commit = _make_commit(subject="Merge docs branch", is_merge_commit=True)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason == AutoClassificationReason.MERGE_COMMIT


# ---------------------------------------------------------------------------
# Rule 2: docs by subject (Spec §8.7)
# ---------------------------------------------------------------------------


class TestRule2DocsBySubject:
    @pytest.mark.parametrize("subject", [
        "Update docs",
        "Fix documentation typo",
        "Update README",
        "Update CHANGELOG",
        "Improve doc coverage",
        "DOC: add examples",
        "DOCS: update guide",
    ])
    def test_docs_subjects_not_sent_to_llm(self, subject):
        commit = _make_commit(subject=subject, size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is False
        assert result.reason == AutoClassificationReason.DOCS_BY_SUBJECT

    def test_docs_category(self):
        commit = _make_commit(subject="Update docs")
        result = _classify(commit, SMALL, BUGFIX)
        assert result.category == LlmCategory.DOCS

    def test_docs_confidence_medium(self):
        commit = _make_commit(subject="Update docs")
        result = _classify(commit, SMALL, BUGFIX)
        assert result.confidence == Confidence.MEDIUM

    def test_docs_not_candidate(self):
        commit = _make_commit(subject="Update docs")
        result = _classify(commit, SMALL, BUGFIX)
        assert result.candidate_for_changelog is False

    def test_non_docs_subject_not_matched(self):
        commit = _make_commit(subject="Add new feature", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason != AutoClassificationReason.DOCS_BY_SUBJECT

    def test_docs_word_boundary(self):
        """'docker' should NOT match the docs rule."""
        commit = _make_commit(subject="Update docker config", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason != AutoClassificationReason.DOCS_BY_SUBJECT

    def test_docs_wins_over_bugfix(self):
        """Rule 2 must win over rule 3 when subject has both 'docs' and 'fix'."""
        commit = _make_commit(subject="Fix docs typo", size_score=50)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason == AutoClassificationReason.DOCS_BY_SUBJECT


# ---------------------------------------------------------------------------
# Rule 3: fix/bug by subject and size (Spec §8.8)
# ---------------------------------------------------------------------------


class TestRule3BugfixBySubjectAndSize:
    @pytest.mark.parametrize("subject", [
        "Fix crash in parser",
        "bug: null pointer",
        "bugfix: handle edge case",
        "hotfix: production issue",
        "FIX: memory leak",
    ])
    def test_bugfix_subjects_under_threshold(self, subject):
        commit = _make_commit(subject=subject, size_score=BUGFIX)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is False
        assert result.reason == AutoClassificationReason.SMALL_OR_MEDIUM_BUGFIX

    def test_bugfix_category_minor_bugfix(self):
        commit = _make_commit(subject="Fix crash", size_score=100)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.category == LlmCategory.MINOR_BUGFIX

    def test_bugfix_confidence_medium(self):
        commit = _make_commit(subject="Fix crash", size_score=100)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.confidence == Confidence.MEDIUM

    def test_bugfix_not_candidate(self):
        commit = _make_commit(subject="Fix crash", size_score=100)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.candidate_for_changelog is False

    def test_bugfix_over_threshold_sent_to_llm(self):
        """Large bugfix (> bugfix_skip) must be sent to LLM."""
        commit = _make_commit(subject="Fix crash", size_score=BUGFIX + 1)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is True

    def test_bugfix_at_threshold_not_sent(self):
        commit = _make_commit(subject="Fix crash", size_score=BUGFIX)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is False

    def test_fix_word_boundary(self):
        """'prefix' should NOT match the bugfix rule."""
        commit = _make_commit(subject="Add prefix support", size_score=50)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason != AutoClassificationReason.SMALL_OR_MEDIUM_BUGFIX


# ---------------------------------------------------------------------------
# Rule 4: small size (Spec §8.9)
# ---------------------------------------------------------------------------


class TestRule4SmallSize:
    def test_small_commit_not_sent_to_llm(self):
        commit = _make_commit(subject="Refactor internals", size_score=SMALL)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is False
        assert result.reason == AutoClassificationReason.SMALL_COMMIT

    def test_small_commit_category(self):
        commit = _make_commit(subject="Refactor internals", size_score=SMALL)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.category == LlmCategory.SMALL

    def test_small_commit_confidence_high(self):
        commit = _make_commit(subject="Refactor internals", size_score=SMALL)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.confidence == Confidence.HIGH

    def test_small_commit_not_candidate(self):
        commit = _make_commit(subject="Refactor internals", size_score=SMALL)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.candidate_for_changelog is False

    def test_one_over_threshold_sent_to_llm(self):
        commit = _make_commit(subject="Refactor internals", size_score=SMALL + 1)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is True

    def test_zero_size_is_small(self):
        commit = _make_commit(subject="Empty commit", size_score=0)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason == AutoClassificationReason.SMALL_COMMIT


# ---------------------------------------------------------------------------
# Rule 5: send to LLM (Spec §8.10)
# ---------------------------------------------------------------------------


class TestRule5SendToLlm:
    def test_large_feature_sent_to_llm(self):
        commit = _make_commit(subject="Add Redis pipelining", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.send_to_llm is True

    def test_send_to_llm_reason(self):
        commit = _make_commit(subject="Add Redis pipelining", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.reason == AutoClassificationReason.REQUIRES_LLM_ANALYSIS

    def test_send_to_llm_category_none(self):
        commit = _make_commit(subject="Add Redis pipelining", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.category is None

    def test_send_to_llm_candidate_none(self):
        commit = _make_commit(subject="Add Redis pipelining", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.candidate_for_changelog is None

    def test_send_to_llm_confidence_none(self):
        commit = _make_commit(subject="Add Redis pipelining", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.confidence is None

    def test_send_to_llm_changelog_line_none(self):
        commit = _make_commit(subject="Add Redis pipelining", size_score=500)
        result = _classify(commit, SMALL, BUGFIX)
        assert result.changelog_line is None


# ---------------------------------------------------------------------------
# preclassify — batch function
# ---------------------------------------------------------------------------


class TestPreclassify:
    def test_all_commits_get_classification(self):
        commits = [
            _make_commit(subject="Merge branch", is_merge_commit=True, sha="a" * 40),
            _make_commit(subject="Update docs", size_score=500, sha="b" * 40),
            _make_commit(subject="Fix crash", size_score=100, sha="c" * 40),
            _make_commit(subject="Tiny tweak", size_score=10, sha="d" * 40),
            _make_commit(subject="Add feature", size_score=500, sha="e" * 40),
        ]
        preclassify(commits, SMALL, BUGFIX)
        for commit in commits:
            assert commit.auto_classification is not None
            assert isinstance(commit.auto_classification.send_to_llm, bool)

    def test_returns_same_list(self):
        commits = [_make_commit()]
        result = preclassify(commits, SMALL, BUGFIX)
        assert result is commits

    def test_correct_rules_applied(self):
        commits = [
            _make_commit(subject="Merge branch", is_merge_commit=True, sha="a" * 40),
            _make_commit(subject="Update docs", size_score=500, sha="b" * 40),
            _make_commit(subject="Fix crash", size_score=100, sha="c" * 40),
            _make_commit(subject="Tiny tweak", size_score=10, sha="d" * 40),
            _make_commit(subject="Add feature", size_score=500, sha="e" * 40),
        ]
        preclassify(commits, SMALL, BUGFIX)
        reasons = [c.auto_classification.reason for c in commits]  # type: ignore[union-attr]
        assert reasons[0] == AutoClassificationReason.MERGE_COMMIT
        assert reasons[1] == AutoClassificationReason.DOCS_BY_SUBJECT
        assert reasons[2] == AutoClassificationReason.SMALL_OR_MEDIUM_BUGFIX
        assert reasons[3] == AutoClassificationReason.SMALL_COMMIT
        assert reasons[4] == AutoClassificationReason.REQUIRES_LLM_ANALYSIS

    def test_empty_list(self):
        result = preclassify([], SMALL, BUGFIX)
        assert result == []

    def test_custom_thresholds(self):
        """Verify thresholds are respected."""
        commit = _make_commit(subject="Refactor", size_score=75)
        # With small_threshold=100, this should be small
        preclassify([commit], small_commit_threshold=100, bugfix_skip_threshold=200)
        assert commit.auto_classification.reason == AutoClassificationReason.SMALL_COMMIT  # type: ignore[union-attr]

    def test_send_to_llm_is_boolean(self):
        commits = [_make_commit(sha=f"{i}" * 40) for i in range(5)]
        preclassify(commits, SMALL, BUGFIX)
        for c in commits:
            assert isinstance(c.auto_classification.send_to_llm, bool)  # type: ignore[union-attr]
