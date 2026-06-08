"""Tests for changelog_tool.models — enums, helpers, dataclasses, serialisation."""

from __future__ import annotations

import dataclasses
import json

import pytest

from changelog_tool.models import (
    AutoClassification,
    AutoClassificationReason,
    ChangelogItem,
    ChangelogSection,
    ClassificationSource,
    CoAuthor,
    Commit,
    Confidence,
    LlmCategory,
    LlmClassification,
    SizeBucket,
    VerifiedCommit,
    category_to_section,
    changelog_item_from_dict,
    commit_from_dict,
    compute_size_bucket,
    compute_size_score,
    to_dict,
    verified_commit_from_dict,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# compute_size_score
# ---------------------------------------------------------------------------


class TestComputeSizeScore:
    def test_zero(self):
        assert compute_size_score(0, 0) == 0

    def test_insertions_only(self):
        assert compute_size_score(100, 0) == 100

    def test_deletions_only(self):
        assert compute_size_score(0, 50) == 50

    def test_both(self):
        assert compute_size_score(240, 31) == 271


# ---------------------------------------------------------------------------
# compute_size_bucket — boundary values (Spec §5.3)
# ---------------------------------------------------------------------------


class TestComputeSizeBucket:
    @pytest.mark.parametrize(
        "score, expected",
        [
            (0, SizeBucket.TINY),
            (10, SizeBucket.TINY),
            (11, SizeBucket.SMALL),
            (50, SizeBucket.SMALL),
            (51, SizeBucket.MEDIUM),
            (200, SizeBucket.MEDIUM),
            (201, SizeBucket.LARGE),
            (1000, SizeBucket.LARGE),
            (1001, SizeBucket.HUGE),
            (99999, SizeBucket.HUGE),
        ],
    )
    def test_boundary(self, score, expected):
        assert compute_size_bucket(score) == expected


# ---------------------------------------------------------------------------
# category_to_section (Spec §11.6)
# ---------------------------------------------------------------------------


class TestCategoryToSection:
    @pytest.mark.parametrize(
        "category, expected_section",
        [
            (LlmCategory.BREAKING_CHANGE, ChangelogSection.BREAKING_CHANGES),
            (LlmCategory.FEATURE, ChangelogSection.FUNCTIONALITY),
            (LlmCategory.IMPROVEMENT, ChangelogSection.IMPROVEMENTS),
            (LlmCategory.IMPORTANT_BUGFIX, ChangelogSection.BUG_FIXES),
            (LlmCategory.DOCS, ChangelogSection.DOCUMENTATION),
        ],
    )
    def test_mapped_categories(self, category, expected_section):
        assert category_to_section(category) == expected_section

    @pytest.mark.parametrize(
        "category",
        [
            LlmCategory.INTERNAL,
            LlmCategory.TESTS,
            LlmCategory.CHORE,
            LlmCategory.UNCLEAR,
            LlmCategory.SMALL,
            LlmCategory.MINOR_BUGFIX,
        ],
    )
    def test_unmapped_categories_return_none(self, category):
        assert category_to_section(category) is None


# ---------------------------------------------------------------------------
# Enum str-inheritance (JSON serialisation without custom encoder)
# ---------------------------------------------------------------------------


class TestEnumStrInheritance:
    def test_size_bucket_json(self):
        assert json.dumps(SizeBucket.LARGE) == '"large"'

    def test_llm_category_json(self):
        assert json.dumps(LlmCategory.FEATURE) == '"feature"'

    def test_changelog_section_json(self):
        assert json.dumps(ChangelogSection.FUNCTIONALITY) == '"Functionality"'


# ---------------------------------------------------------------------------
# Helpers: _make_minimal_commit
# ---------------------------------------------------------------------------


def _make_minimal_commit(**overrides) -> Commit:
    defaults = dict(
        sha="a" * 40,
        short_sha="a" * 8,
        is_merge_commit=False,
        author_name="Alice",
        author_email="alice@example.com",
        co_authors=[],
        subject="Add feature X",
        body="",
        message="Add feature X",
        commit_url="https://github.com/org/repo/commit/" + "a" * 40,
        changed_files=["src/x.cpp"],
        insertions=100,
        deletions=20,
        files_count=1,
        size_score=120,
        size_bucket=SizeBucket.MEDIUM,
    )
    defaults.update(overrides)
    return Commit(**defaults)


# ---------------------------------------------------------------------------
# to_dict — basic round-trip
# ---------------------------------------------------------------------------


class TestToDict:
    def test_commit_is_json_serialisable(self):
        commit = _make_minimal_commit()
        d = to_dict(commit)
        # Must not raise
        serialised = json.dumps(d)
        assert '"sha"' in serialised

    def test_enum_values_are_strings(self):
        commit = _make_minimal_commit()
        d = to_dict(commit)
        assert d["size_bucket"] == "medium"

    def test_co_author_nested(self):
        commit = _make_minimal_commit(
            co_authors=[CoAuthor(name="Bob", email="bob@example.com")]
        )
        d = to_dict(commit)
        assert d["co_authors"][0]["name"] == "Bob"
        assert "github_login" not in d["co_authors"][0]

    def test_auto_classification_nested(self):
        ac = AutoClassification(
            send_to_llm=False,
            reason=AutoClassificationReason.SMALL_COMMIT,
            category=LlmCategory.SMALL,
            candidate_for_changelog=False,
            changelog_line=None,
            confidence=Confidence.HIGH,
        )
        commit = _make_minimal_commit(auto_classification=ac)
        d = to_dict(commit)
        assert d["auto_classification"]["reason"] == "small_commit"
        assert d["auto_classification"]["category"] == "small"
        assert d["auto_classification"]["confidence"] == "high"

    def test_none_optional_fields(self):
        commit = _make_minimal_commit()
        d = to_dict(commit)
        assert d["is_external"] is None
        assert d["auto_classification"] is None
        assert d["llm_classification"] is None


# ---------------------------------------------------------------------------
# commit_from_dict — round-trip
# ---------------------------------------------------------------------------


class TestCommitFromDict:
    def _round_trip(self, commit: Commit) -> Commit:
        return commit_from_dict(to_dict(commit))

    def test_minimal_round_trip(self):
        original = _make_minimal_commit()
        restored = self._round_trip(original)
        assert restored.sha == original.sha
        assert restored.size_bucket == SizeBucket.MEDIUM
        assert restored.co_authors == []

    def test_with_co_author(self):
        original = _make_minimal_commit(
            co_authors=[CoAuthor(name="Bob", email="bob@example.com", is_external=True)]
        )
        restored = self._round_trip(original)
        assert len(restored.co_authors) == 1
        ca = restored.co_authors[0]
        assert ca.name == "Bob"
        assert ca.is_external is True

    def test_with_auto_classification(self):
        ac = AutoClassification(
            send_to_llm=True,
            reason=AutoClassificationReason.REQUIRES_LLM_ANALYSIS,
            category=None,
            candidate_for_changelog=None,
            changelog_line=None,
            confidence=None,
        )
        original = _make_minimal_commit(auto_classification=ac)
        restored = self._round_trip(original)
        assert restored.auto_classification is not None
        assert restored.auto_classification.send_to_llm is True
        assert restored.auto_classification.category is None

    def test_with_llm_classification(self):
        lc = LlmClassification(
            category=LlmCategory.FEATURE,
            candidate_for_changelog=True,
            changelog_line="Added feature X.",
            comment="User-facing change.",
            confidence=Confidence.HIGH,
        )
        original = _make_minimal_commit(llm_classification=lc)
        restored = self._round_trip(original)
        assert restored.llm_classification is not None
        assert restored.llm_classification.category == LlmCategory.FEATURE
        assert restored.llm_classification.changelog_line == "Added feature X."

    def test_is_external_field(self):
        original = _make_minimal_commit(is_external=False)
        restored = self._round_trip(original)
        assert restored.is_external is False


# ---------------------------------------------------------------------------
# verified_commit_from_dict — round-trip
# ---------------------------------------------------------------------------


def _make_verified_commit(**overrides) -> VerifiedCommit:
    defaults = dict(
        sha="b" * 40,
        short_sha="b" * 8,
        subject="Improve performance",
        body="",
        commit_url="https://github.com/org/repo/commit/" + "b" * 40,
        author_name="Carol",
        author_email="carol@example.com",
        is_external=False,
        co_authors=[],
        changed_files=["src/perf.cpp"],
        insertions=50,
        deletions=10,
        files_count=1,
        size_score=60,
        size_bucket=SizeBucket.MEDIUM,
        classification_source=ClassificationSource.LLM,
        category=LlmCategory.IMPROVEMENT,
        candidate_for_changelog=True,
        changelog_line="Improved performance of X.",
        classification_comment="Noticeable user-facing improvement.",
        confidence=Confidence.HIGH,
    )
    defaults.update(overrides)
    return VerifiedCommit(**defaults)


class TestVerifiedCommitFromDict:
    def test_round_trip(self):
        original = _make_verified_commit()
        restored = verified_commit_from_dict(to_dict(original))
        assert restored.sha == original.sha
        assert restored.category == LlmCategory.IMPROVEMENT
        assert restored.classification_source == ClassificationSource.LLM
        assert restored.confidence == Confidence.HIGH
        assert restored.attention_flags == []

    def test_attention_flags_preserved(self):
        original = _make_verified_commit(attention_flags=["unclear", "unknown_login"])
        restored = verified_commit_from_dict(to_dict(original))
        assert restored.attention_flags == ["unclear", "unknown_login"]

    def test_is_merge_commit_default_false(self):
        original = _make_verified_commit()
        d = to_dict(original)
        # Remove the field to test default
        d.pop("is_merge_commit", None)
        restored = verified_commit_from_dict(d)
        assert restored.is_merge_commit is False


# ---------------------------------------------------------------------------
# changelog_item_from_dict — round-trip
# ---------------------------------------------------------------------------


def _make_changelog_item(**overrides) -> ChangelogItem:
    defaults = dict(
        sha="c" * 40,
        short_sha="c" * 8,
        category=LlmCategory.FEATURE,
        changelog_section=ChangelogSection.FUNCTIONALITY,
        changelog_line="Added Redis pipelining support.",
        author_name="Alice",
        author_email="alice@example.com",
        co_authors=[],
        is_external=False,
        commit_url="https://github.com/org/repo/commit/" + "c" * 40,
        source=ClassificationSource.LLM,
    )
    defaults.update(overrides)
    return ChangelogItem(**defaults)


class TestChangelogItemFromDict:
    def test_round_trip(self):
        original = _make_changelog_item()
        restored = changelog_item_from_dict(to_dict(original))
        assert restored.sha == original.sha
        assert restored.category == LlmCategory.FEATURE
        assert restored.changelog_section == ChangelogSection.FUNCTIONALITY
        assert restored.source == ClassificationSource.LLM

    def test_maintainer_comment_preserved(self):
        original = _make_changelog_item(maintainer_comment="Great addition!")
        restored = changelog_item_from_dict(to_dict(original))
        assert restored.maintainer_comment == "Great addition!"

    def test_attention_flags_preserved(self):
        original = _make_changelog_item(attention_flags=["todo_line"])
        restored = changelog_item_from_dict(to_dict(original))
        assert restored.attention_flags == ["todo_line"]


# ---------------------------------------------------------------------------
# Dataclass field completeness — ensure to_dict captures all fields
# ---------------------------------------------------------------------------


class TestFieldCompleteness:
    def test_commit_all_fields_in_dict(self):
        commit = _make_minimal_commit()
        d = to_dict(commit)
        for field in dataclasses.fields(Commit):
            assert field.name in d, f"Field '{field.name}' missing from to_dict output"

    def test_verified_commit_all_fields_in_dict(self):
        vc = _make_verified_commit()
        d = to_dict(vc)
        for field in dataclasses.fields(VerifiedCommit):
            assert field.name in d, f"Field '{field.name}' missing from to_dict output"

    def test_changelog_item_all_fields_in_dict(self):
        item = _make_changelog_item()
        d = to_dict(item)
        for field in dataclasses.fields(ChangelogItem):
            assert field.name in d, f"Field '{field.name}' missing from to_dict output"
