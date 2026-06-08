"""Tests for changelog_tool.review_formatter — Stage 5.2."""

from __future__ import annotations

import os

from changelog_tool.models import (
    ClassificationSource,
    CoAuthor,
    Confidence,
    LlmCategory,
    SizeBucket,
    VerifiedCommit,
)
from changelog_tool.review_formatter import (
    generate_markdown_report,
    generate_yaml_overrides_template,
    handle_overrides_file,
)


def _make_verified_commit(
    sha: str,
    size_score: int,
    attention_flags: list[str] | None = None,
) -> VerifiedCommit:
    return VerifiedCommit(
        sha=sha,
        short_sha=sha[:8],
        subject=f"Subject for {sha[:8]}",
        body="Body text",
        commit_url=f"https://github.com/org/repo/commit/{sha}",
        author_name="Author Name",
        author_email="author@example.com",
        is_external=False,
        co_authors=[CoAuthor(name="Co", email="co@example.com", is_external=True)],
        changed_files=["file1.txt", "file2.txt"],
        insertions=size_score,
        deletions=0,
        files_count=2,
        size_score=size_score,
        size_bucket=SizeBucket.SMALL,
        classification_source=ClassificationSource.LLM,
        category=LlmCategory.FEATURE,
        candidate_for_changelog=True,
        changelog_line="Added feature",
        classification_comment="Good feature",
        confidence=Confidence.HIGH,
        is_merge_commit=False,
        attention_flags=attention_flags or [],
    )


def test_generate_markdown_report(tmp_path):
    c1 = _make_verified_commit("1" * 40, size_score=10)
    c2 = _make_verified_commit("2" * 40, size_score=100, attention_flags=["category_unclear"])

    out_path = tmp_path / "report.md"
    generate_markdown_report([c1, c2], str(out_path))

    content = out_path.read_text(encoding="utf-8")

    # Check sorting (c2 is larger, should be first)
    idx1 = content.find("22222222")
    idx2 = content.find("11111111")
    assert idx1 < idx2

    # Check attention section
    assert "## ⚠️ Requires Attention" in content
    assert "- [`22222222`](#22222222) — Subject for 22222222 (`category_unclear`)" in content

    # Check commit details
    assert "### [Subject for 22222222](https://github.com/org/repo/commit/2222222222222222222222222222222222222222) (`22222222`)" in content
    assert "**Size:** 100 lines (small) | **Author:** Author Name (core-team)" in content
    assert "**Category:** `feature` | **Source:** `llm` | **Confidence:** `high`" in content
    assert "**Include in CHANGELOG:** ✅ Yes" in content
    assert "**Proposed Line:** Added feature" in content
    assert "**LLM Comment:** Good feature" in content
    assert "**⚠️ Attention:** `category_unclear`" in content
    assert "**Changed Files (2):**" in content
    assert "- `file1.txt`" in content


def test_generate_yaml_overrides_template():
    c1 = _make_verified_commit("1" * 40, size_score=10)
    c2 = _make_verified_commit("2" * 40, size_score=100, attention_flags=["category_unclear"])

    content = generate_yaml_overrides_template([c1, c2])

    # Check sorting
    idx1 = content.find("2222222222222222222222222222222222222222:")
    idx2 = content.find("1111111111111111111111111111111111111111:")
    assert idx1 < idx2

    # Check attention flag comment
    assert "  # ⚠️ REQUIRES ATTENTION: category_unclear" in content

    # Check commit block
    assert "  # Subject for 22222222" in content
    assert "  # Size: 100 lines | Link: https://github.com/org/repo/commit/2222222222222222222222222222222222222222" in content
    assert "  # 2222222222222222222222222222222222222222:" in content
    assert "  #   category: feature" in content
    assert "  #   candidate_for_changelog: true" in content
    assert '  #   changelog_line: "Added feature"' in content
    assert '  #   maintainer_comment: ""' in content


def test_handle_overrides_file_create(tmp_path):
    out_path = tmp_path / "overrides.yaml"
    c1 = _make_verified_commit("1" * 40, size_score=10)

    handle_overrides_file([c1], str(out_path))

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "1111111111111111111111111111111111111111:" in content


def test_handle_overrides_file_keep(tmp_path):
    out_path = tmp_path / "overrides.yaml"
    out_path.write_text("existing content", encoding="utf-8")

    c1 = _make_verified_commit("1" * 40, size_score=10)
    handle_overrides_file([c1], str(out_path), action="keep")

    assert out_path.read_text(encoding="utf-8") == "existing content"


def test_handle_overrides_file_backup(tmp_path):
    out_path = tmp_path / "overrides.yaml"
    out_path.write_text("existing content", encoding="utf-8")

    c1 = _make_verified_commit("1" * 40, size_score=10)
    handle_overrides_file([c1], str(out_path), action="backup-and-regenerate")

    assert out_path.read_text(encoding="utf-8") != "existing content"
    assert "1111111111111111111111111111111111111111:" in out_path.read_text(encoding="utf-8")

    backup_path = tmp_path / "overrides.yaml.bak"
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == "existing content"


def test_handle_overrides_file_merge(tmp_path):
    out_path = tmp_path / "overrides.yaml"
    out_path.write_text("existing content\n", encoding="utf-8")

    c1 = _make_verified_commit("1" * 40, size_score=10)
    handle_overrides_file([c1], str(out_path), action="merge")

    content = out_path.read_text(encoding="utf-8")
    assert content.startswith("existing content\n")
    assert "# --- Merged Template ---" in content
    assert "1111111111111111111111111111111111111111:" in content
