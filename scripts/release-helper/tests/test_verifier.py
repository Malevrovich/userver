"""Tests for changelog_tool.verifier — Stage 5.1."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

from changelog_tool.io import compute_sha_checksum
from changelog_tool.models import (
    AutoClassification,
    AutoClassificationReason,
    ClassificationSource,
    CoAuthor,
    Commit,
    Confidence,
    LlmCategory,
    LlmClassification,
    SizeBucket,
    to_dict,
)
from changelog_tool.verifier import (
    ATTENTION_COAUTHOR_EXTERNAL_NO_PROFILE,
    ATTENTION_COAUTHOR_UNKNOWN_LOGIN,
    ATTENTION_EXTERNAL_NO_PROFILE,
    ATTENTION_LLM_FALLBACK,
    ATTENTION_LOW_CONFIDENCE,
    ATTENTION_MERGE_COMMIT,
    ATTENTION_MISSING_CHANGELOG_LINE,
    ATTENTION_UNCLEAR,
    VerificationError,
    run_verification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(
    sha: str,
    send_to_llm: bool,
    reason: AutoClassificationReason,
    category: LlmCategory | None = None,
    candidate: bool | None = None,
    confidence: Confidence | None = None,
    is_merge_commit: bool = False,
    is_external: bool = False,
    author_email: str = "author@example.com",
    co_authors: List[CoAuthor] | None = None,
) -> Commit:
    return Commit(
        sha=sha,
        short_sha=sha[:8],
        is_merge_commit=is_merge_commit,
        author_name="Author",
        author_email=author_email,
        co_authors=co_authors or [],
        subject="Subject",
        body="",
        message="Subject",
        commit_url=f"https://github.com/org/repo/commit/{sha}",
        changed_files=[],
        insertions=10,
        deletions=0,
        files_count=1,
        size_score=10,
        size_bucket=SizeBucket.SMALL,
        is_external=is_external,
        auto_classification=AutoClassification(
            send_to_llm=send_to_llm,
            reason=reason,
            category=category,
            candidate_for_changelog=candidate,
            changelog_line=None,
            confidence=confidence,
        ),
    )


def _make_llm_result(
    category: LlmCategory,
    candidate: bool,
    changelog_line: str | None = None,
    comment: str | None = None,
    confidence: Confidence = Confidence.HIGH,
) -> LlmClassification:
    return LlmClassification(
        category=category,
        candidate_for_changelog=candidate,
        changelog_line=changelog_line,
        comment=comment,
        confidence=confidence,
    )


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _write_jsonl(path: str, records: List[Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_successful_verification(tmp_path):
    """Test a successful run with both auto and LLM classifications."""
    workdir = tmp_path / ".changelog"
    workdir.mkdir()

    sha1 = "1" * 40
    sha2 = "2" * 40

    # 1. Manifest
    manifest = {
        "total_commits": 2,
        "sha_list": [sha1, sha2],
        "sha_checksum": compute_sha_checksum([sha1, sha2]),
    }
    _write_json(workdir / "01_commits_manifest.json", manifest)

    # 2. Preclassified
    c1 = _make_commit(
        sha1,
        send_to_llm=False,
        reason=AutoClassificationReason.SMALL_COMMIT,
        category=LlmCategory.SMALL,
        candidate=False,
        confidence=Confidence.HIGH,
    )
    c2 = _make_commit(
        sha2,
        send_to_llm=True,
        reason=AutoClassificationReason.REQUIRES_LLM_ANALYSIS,
    )
    _write_jsonl(workdir / "03_preclassified.jsonl", [to_dict(c1), to_dict(c2)])

    # 3. LLM classified
    llm2 = _make_llm_result(LlmCategory.FEATURE, True, "Added feature")
    _write_jsonl(
        workdir / "04_llm_classified.jsonl",
        [{"sha": sha2, "llm_classification": to_dict(llm2)}],
    )

    # Run
    result = run_verification(
        str(workdir / "01_commits_manifest.json"),
        str(workdir / "03_preclassified.jsonl"),
        str(workdir / "04_llm_classified.jsonl"),
        str(workdir / "05_verified_classification.jsonl"),
        str(workdir / "05_verification_report.json"),
    )

    assert result.checks.passed is True
    assert len(result.verified) == 2

    # Check merged results
    v1 = next(v for v in result.verified if v.sha == sha1)
    assert v1.classification_source == ClassificationSource.AUTO
    assert v1.category == LlmCategory.SMALL

    v2 = next(v for v in result.verified if v.sha == sha2)
    assert v2.classification_source == ClassificationSource.LLM
    assert v2.category == LlmCategory.FEATURE
    assert v2.changelog_line == "Added feature"

    # Check report
    report = read_json(str(workdir / "05_verification_report.json"))
    assert report["passed"] is True
    assert report["stats"]["sent_to_llm"] == 1
    assert report["stats"]["skipped_by_size"] == 1
    assert report["stats"]["candidates_for_changelog"] == 1


def test_missing_llm_result(tmp_path):
    """Test failure when a commit sent to LLM has no result."""
    workdir = tmp_path / ".changelog"
    workdir.mkdir()

    sha1 = "1" * 40

    manifest = {
        "total_commits": 1,
        "sha_list": [sha1],
        "sha_checksum": compute_sha_checksum([sha1]),
    }
    _write_json(workdir / "01_commits_manifest.json", manifest)

    c1 = _make_commit(
        sha1,
        send_to_llm=True,
        reason=AutoClassificationReason.REQUIRES_LLM_ANALYSIS,
    )
    _write_jsonl(workdir / "03_preclassified.jsonl", [to_dict(c1)])

    # Empty LLM results
    _write_jsonl(workdir / "04_llm_classified.jsonl", [])

    with pytest.raises(VerificationError, match="Run `changelog-tool collect`"):
        run_verification(
            str(workdir / "01_commits_manifest.json"),
            str(workdir / "03_preclassified.jsonl"),
            str(workdir / "04_llm_classified.jsonl"),
            str(workdir / "05_verified_classification.jsonl"),
            str(workdir / "05_verification_report.json"),
        )


def test_duplicate_sha(tmp_path):
    """Test failure when duplicate SHAs exist."""
    workdir = tmp_path / ".changelog"
    workdir.mkdir()

    sha1 = "1" * 40

    manifest = {
        "total_commits": 1,
        "sha_list": [sha1],
        "sha_checksum": compute_sha_checksum([sha1]),
    }
    _write_json(workdir / "01_commits_manifest.json", manifest)

    c1 = _make_commit(
        sha1,
        send_to_llm=False,
        reason=AutoClassificationReason.SMALL_COMMIT,
        category=LlmCategory.SMALL,
        candidate=False,
        confidence=Confidence.HIGH,
    )
    # Duplicate in preclassified
    _write_jsonl(workdir / "03_preclassified.jsonl", [to_dict(c1), to_dict(c1)])
    _write_jsonl(workdir / "04_llm_classified.jsonl", [])

    with pytest.raises(VerificationError, match="duplicate SHA found"):
        run_verification(
            str(workdir / "01_commits_manifest.json"),
            str(workdir / "03_preclassified.jsonl"),
            str(workdir / "04_llm_classified.jsonl"),
            str(workdir / "05_verified_classification.jsonl"),
            str(workdir / "05_verification_report.json"),
        )


def test_sha_set_mismatch(tmp_path):
    """Test failure when SHAs don't match manifest."""
    workdir = tmp_path / ".changelog"
    workdir.mkdir()

    sha1 = "1" * 40
    sha2 = "2" * 40

    manifest = {
        "total_commits": 2,
        "sha_list": [sha1, sha2],
        "sha_checksum": compute_sha_checksum([sha1, sha2]),
    }
    _write_json(workdir / "01_commits_manifest.json", manifest)

    # Only c1 is present
    c1 = _make_commit(
        sha1,
        send_to_llm=False,
        reason=AutoClassificationReason.SMALL_COMMIT,
        category=LlmCategory.SMALL,
        candidate=False,
        confidence=Confidence.HIGH,
    )
    _write_jsonl(workdir / "03_preclassified.jsonl", [to_dict(c1)])
    _write_jsonl(workdir / "04_llm_classified.jsonl", [])

    with pytest.raises(VerificationError, match="SHA set mismatch"):
        run_verification(
            str(workdir / "01_commits_manifest.json"),
            str(workdir / "03_preclassified.jsonl"),
            str(workdir / "04_llm_classified.jsonl"),
            str(workdir / "05_verified_classification.jsonl"),
            str(workdir / "05_verification_report.json"),
        )


def test_attention_flags(tmp_path):
    """Test that attention flags are correctly assigned."""
    workdir = tmp_path / ".changelog"
    workdir.mkdir()

    sha1 = "1" * 40

    manifest = {
        "total_commits": 1,
        "sha_list": [sha1],
        "sha_checksum": compute_sha_checksum([sha1]),
    }
    _write_json(workdir / "01_commits_manifest.json", manifest)

    # Commit with multiple issues:
    # - merge commit
    # - external author with no email (proxy for no profile)
    # - co-author with no email
    c1 = _make_commit(
        sha1,
        send_to_llm=True,
        reason=AutoClassificationReason.REQUIRES_LLM_ANALYSIS,
        is_merge_commit=True,
        is_external=True,
        author_email="",
        co_authors=[CoAuthor(name="Co", email="", is_external=True)],
    )
    _write_jsonl(workdir / "03_preclassified.jsonl", [to_dict(c1)])

    # LLM result with issues:
    # - unclear category
    # - low confidence
    # - candidate but no changelog line
    llm1 = _make_llm_result(
        category=LlmCategory.UNCLEAR,
        candidate=True,
        changelog_line="",
        confidence=Confidence.LOW,
    )
    _write_jsonl(
        workdir / "04_llm_classified.jsonl",
        [{"sha": sha1, "llm_classification": to_dict(llm1)}],
    )

    result = run_verification(
        str(workdir / "01_commits_manifest.json"),
        str(workdir / "03_preclassified.jsonl"),
        str(workdir / "04_llm_classified.jsonl"),
        str(workdir / "05_verified_classification.jsonl"),
        str(workdir / "05_verification_report.json"),
    )

    vc = result.verified[0]
    flags = vc.attention_flags

    assert ATTENTION_MERGE_COMMIT in flags
    assert ATTENTION_EXTERNAL_NO_PROFILE in flags
    assert ATTENTION_COAUTHOR_EXTERNAL_NO_PROFILE in flags
    assert ATTENTION_UNCLEAR in flags
    assert ATTENTION_LOW_CONFIDENCE in flags
    assert ATTENTION_MISSING_CHANGELOG_LINE in flags
    assert ATTENTION_LLM_FALLBACK in flags


def test_no_llm_file_needed_if_no_commits_sent(tmp_path):
    """Test that missing 04_llm_classified.jsonl is fine if no commits sent to LLM."""
    workdir = tmp_path / ".changelog"
    workdir.mkdir()

    sha1 = "1" * 40

    manifest = {
        "total_commits": 1,
        "sha_list": [sha1],
        "sha_checksum": compute_sha_checksum([sha1]),
    }
    _write_json(workdir / "01_commits_manifest.json", manifest)

    c1 = _make_commit(
        sha1,
        send_to_llm=False,
        reason=AutoClassificationReason.SMALL_COMMIT,
        category=LlmCategory.SMALL,
        candidate=False,
        confidence=Confidence.HIGH,
    )
    _write_jsonl(workdir / "03_preclassified.jsonl", [to_dict(c1)])

    # Do NOT create 04_llm_classified.jsonl

    result = run_verification(
        str(workdir / "01_commits_manifest.json"),
        str(workdir / "03_preclassified.jsonl"),
        str(workdir / "04_llm_classified.jsonl"),
        str(workdir / "05_verified_classification.jsonl"),
        str(workdir / "05_verification_report.json"),
    )

    assert result.checks.passed is True
    assert len(result.verified) == 1

def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
