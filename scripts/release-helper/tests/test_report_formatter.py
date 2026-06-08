"""Tests for changelog_tool.report_formatter."""

from __future__ import annotations

import os

from changelog_tool.llm_generator import TODO_FALLBACK_PREFIX
from changelog_tool.models import (
    ChangelogItem,
    ChangelogSection,
    ClassificationSource,
    CoAuthor,
    Confidence,
    LlmCategory,
    SizeBucket,
    VerifiedCommit,
)
from changelog_tool.report_formatter import generate_final_report


def _make_verified_commit(sha: str, attention_flags: list[str] | None = None) -> VerifiedCommit:
    return VerifiedCommit(
        sha=sha,
        short_sha=sha[:8],
        subject=f"Subject for {sha[:8]}",
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
        classification_source=ClassificationSource.LLM,
        category=LlmCategory.FEATURE,
        candidate_for_changelog=True,
        changelog_line="Added feature",
        classification_comment=None,
        confidence=Confidence.HIGH,
        is_merge_commit=False,
        attention_flags=attention_flags or [],
    )


def _make_changelog_item(sha: str, section: ChangelogSection, line: str) -> ChangelogItem:
    return ChangelogItem(
        sha=sha,
        short_sha=sha[:8],
        category=LlmCategory.FEATURE,
        changelog_section=section,
        changelog_line=line,
        author_name="Author",
        author_email="author@example.com",
        co_authors=[],
        is_external=False,
        commit_url=f"https://github.com/org/repo/commit/{sha}",
        source=ClassificationSource.LLM,
        maintainer_comment=None,
        attention_flags=[],
    )


def test_generate_final_report(tmp_path):
    c1 = _make_verified_commit("1" * 40)
    c2 = _make_verified_commit("2" * 40, attention_flags=["category_unclear"])

    i1 = _make_changelog_item("1" * 40, ChangelogSection.FUNCTIONALITY, "Added feature 1")
    i2 = _make_changelog_item("2" * 40, ChangelogSection.BUG_FIXES, f"{TODO_FALLBACK_PREFIX} Fix bug")

    ext_contrib_md = "- Ivan Petrov"
    verification_report = {
        "passed": True,
        "checks": {"same_sha_set": True},
        "stats": {"sent_to_llm": 2, "candidates_for_changelog": 2, "external_contributors": 1},
        "output": {"total_commits": 2},
    }

    out_path = tmp_path / "final_report.md"
    generate_final_report([i1, i2], [c1, c2], ext_contrib_md, verification_report, str(out_path))

    content = out_path.read_text(encoding="utf-8")

    # Check sections
    assert "## 1. Proposed CHANGELOG" in content
    assert "### Functionality" in content
    assert "- Added feature 1 ([`11111111`](https://github.com/org/repo/commit/1111111111111111111111111111111111111111))" in content
    assert "### Bug fixes" in content
    assert f"- {TODO_FALLBACK_PREFIX} Fix bug ([`22222222`]" in content

    assert "## 2. External Contributors" in content
    assert "- Ivan Petrov" in content

    assert "## 3. Verification Status" in content
    assert "**Status:** ✅ PASSED" in content
    assert "- ✅ `same_sha_set`" in content

    assert "## 4. Statistics" in content
    assert "- **Total commits:** 2" in content
    assert "- **Sent to LLM:** 2" in content

    assert "## 5. Human Attention Items" in content
    assert f"- ⚠️ **Missing CHANGELOG line:** [`22222222`]" in content
    assert f"- ⚠️ **Attention flags:** [`22222222`]" in content

    assert "## 6. Appendix: All Commits" in content
    assert "- [`11111111`]" in content
    assert "- [`22222222`]" in content
