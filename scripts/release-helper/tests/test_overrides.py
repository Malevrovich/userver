"""Tests for changelog_tool.overrides."""

from __future__ import annotations

import pytest

from changelog_tool.models import (
    ClassificationSource,
    Confidence,
    LlmCategory,
    SizeBucket,
    VerifiedCommit,
)
from changelog_tool.overrides import OverridesError, apply_overrides


def _make_verified_commit(sha: str) -> VerifiedCommit:
    return VerifiedCommit(
        sha=sha,
        short_sha=sha[:8],
        subject="Subject",
        body="",
        commit_url=f"https://github.com/org/repo/commit/{sha}",
        author_name="Author",
        author_email="author@example.com",
        is_external=False,
        co_authors=[],
        changed_files=[],
        insertions=10,
        deletions=0,
        files_count=1,
        size_score=10,
        size_bucket=SizeBucket.SMALL,
        classification_source=ClassificationSource.AUTO,
        category=LlmCategory.UNCLEAR,
        candidate_for_changelog=False,
        changelog_line=None,
        classification_comment=None,
        confidence=Confidence.LOW,
        is_merge_commit=False,
        attention_flags=[],
    )


def test_apply_overrides_success(tmp_path):
    c1 = _make_verified_commit("1" * 40)
    c2 = _make_verified_commit("2" * 40)

    yaml_content = f"""
overrides:
  {"1" * 40}:
    category: feature
    candidate_for_changelog: true
    changelog_line: "Added feature"
    maintainer_comment: "Good"
"""
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(yaml_content, encoding="utf-8")

    count = apply_overrides([c1, c2], str(overrides_path))

    assert count == 1
    assert c1.category == LlmCategory.FEATURE
    assert c1.candidate_for_changelog is True
    assert c1.changelog_line == "Added feature"
    assert c1.classification_comment == "Good"

    # c2 should be unchanged
    assert c2.category == LlmCategory.UNCLEAR


def test_apply_overrides_missing_file(tmp_path):
    c1 = _make_verified_commit("1" * 40)
    count = apply_overrides([c1], str(tmp_path / "missing.yaml"))
    assert count == 0


def test_apply_overrides_unknown_sha(tmp_path):
    c1 = _make_verified_commit("1" * 40)

    yaml_content = f"""
overrides:
  {"2" * 40}:
    category: feature
"""
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(OverridesError, match="override references unknown SHA"):
        apply_overrides([c1], str(overrides_path))


def test_apply_overrides_invalid_yaml(tmp_path):
    c1 = _make_verified_commit("1" * 40)

    yaml_content = """
overrides:
  - invalid list instead of dict
"""
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(OverridesError, match="must be a mapping"):
        apply_overrides([c1], str(overrides_path))


def test_apply_overrides_invalid_category(tmp_path):
    c1 = _make_verified_commit("1" * 40)

    yaml_content = f"""
overrides:
  {"1" * 40}:
    category: invalid_category
"""
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(OverridesError, match="invalid category"):
        apply_overrides([c1], str(overrides_path))
