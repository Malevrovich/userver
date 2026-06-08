"""Stage 5.1: Verification and merge of auto/LLM classifications (Spec §10.1).

This module:

1. Reads the original SHA manifest (``01_commits_manifest.json``).
2. Reads the pre-classified commits (``03_preclassified.jsonl``).
3. Reads the LLM-classified results (``04_llm_classified.jsonl``), if any.
4. Merges the two classification sources into unified :class:`VerifiedCommit`
   objects using the rule:
       - ``auto_classification.send_to_llm == False``  → use auto_classification
       - ``auto_classification.send_to_llm == True``   → use llm_classification
5. Runs mandatory integrity checks (Spec §10.1 "Mandatory checks").
6. Writes ``05_verified_classification.jsonl`` and
   ``05_verification_report.json``.

Raises :class:`VerificationError` if any mandatory check fails.

Spec references
---------------
§10.1 — Stage 5.1: Verification
§14.1 — Lost SHA
§14.2 — Duplicate SHA values
"""

from __future__ import annotations

import dataclasses
import datetime
import json
from collections import Counter
from typing import Any, Dict, List, Optional, Set

from changelog_tool.io import (
    compute_sha_checksum,
    read_json,
    read_jsonl,
    write_json_atomic,
    write_jsonl,
)
from changelog_tool.models import (
    ClassificationSource,
    Commit,
    Confidence,
    LlmCategory,
    LlmClassification,
    VerifiedCommit,
    commit_from_dict,
    to_dict,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VerificationError(Exception):
    """Raised when a mandatory pipeline integrity check fails (Spec §14.1–14.2)."""


# ---------------------------------------------------------------------------
# Attention-flag constants (Spec §10.2.3)
# ---------------------------------------------------------------------------

ATTENTION_UNCLEAR = "category_unclear"
ATTENTION_LOW_CONFIDENCE = "confidence_low"
ATTENTION_MISSING_CHANGELOG_LINE = "candidate_but_no_changelog_line"
ATTENTION_UNKNOWN_LOGIN = "github_login_unknown"
ATTENTION_EXTERNAL_NO_PROFILE = "external_contributor_no_profile_url"
ATTENTION_COAUTHOR_UNKNOWN_LOGIN = "coauthor_github_login_unknown"
ATTENTION_COAUTHOR_EXTERNAL_NO_PROFILE = "coauthor_external_no_profile_url"
ATTENTION_MERGE_COMMIT = "merge_commit"
ATTENTION_LLM_FALLBACK = "llm_fallback_unclear"


# ---------------------------------------------------------------------------
# LLM classification deserialisation (local, avoids importing private symbol)
# ---------------------------------------------------------------------------


def _parse_llm_classification(d: Dict[str, Any]) -> LlmClassification:
    """Deserialise a raw dict into a :class:`LlmClassification`."""
    return LlmClassification(
        category=LlmCategory(d.get("category", "unclear")),
        candidate_for_changelog=bool(d.get("candidate_for_changelog", False)),
        changelog_line=d.get("changelog_line"),
        comment=d.get("comment"),
        confidence=Confidence(d.get("confidence", "low")),
    )


# ---------------------------------------------------------------------------
# Merge logic (Spec §10.1 "Merge rules")
# ---------------------------------------------------------------------------


def _merge_commit(
    commit: Commit,
    llm_map: Dict[str, LlmClassification],
) -> VerifiedCommit:
    """Produce a :class:`VerifiedCommit` from a pre-classified :class:`Commit`.

    Merge rule (Spec §10.1):
    - If ``auto_classification.send_to_llm == False`` → use auto_classification.
    - If ``auto_classification.send_to_llm == True``  → use llm_classification
      from *llm_map*.

    Args:
        commit:  A commit with ``auto_classification`` set.
        llm_map: SHA → :class:`LlmClassification` mapping from stage 4.

    Returns:
        A fully populated :class:`VerifiedCommit`.

    Raises:
        VerificationError: If ``send_to_llm`` is True but no LLM result exists.
    """
    ac = commit.auto_classification
    if ac is None:
        raise VerificationError(
            f"Commit {commit.sha} has no auto_classification — "
            "stage 3 (pre-classification) must run before stage 5."
        )

    if not ac.send_to_llm:
        # Use auto classification result.
        source = ClassificationSource.AUTO
        # auto_classification always has category/candidate/confidence set when
        # send_to_llm is False (guaranteed by the classifier).
        category: LlmCategory = ac.category  # type: ignore[assignment]
        candidate: bool = bool(ac.candidate_for_changelog)
        changelog_line: Optional[str] = ac.changelog_line
        comment: Optional[str] = None
        confidence: Confidence = ac.confidence  # type: ignore[assignment]
    else:
        # Use LLM classification result.
        lc = llm_map.get(commit.sha)
        if lc is None:
            raise VerificationError(
                f"Commit {commit.sha} was sent to the LLM but has no LLM "
                "classification result. Run `changelog-tool collect` to "
                "complete LLM classification before running `review`."
            )
        source = ClassificationSource.LLM
        category = lc.category
        candidate = lc.candidate_for_changelog
        changelog_line = lc.changelog_line
        comment = lc.comment
        confidence = lc.confidence

    # Resolve is_external: default to False if not set (core-team member).
    is_external = bool(commit.is_external) if commit.is_external is not None else False

    vc = VerifiedCommit(
        sha=commit.sha,
        short_sha=commit.short_sha,
        subject=commit.subject,
        body=commit.body,
        commit_url=commit.commit_url,
        author_name=commit.author_name,
        author_email=commit.author_email,
        is_external=is_external,
        co_authors=commit.co_authors,
        changed_files=commit.changed_files,
        insertions=commit.insertions,
        deletions=commit.deletions,
        files_count=commit.files_count,
        size_score=commit.size_score,
        size_bucket=commit.size_bucket,
        classification_source=source,
        category=category,
        candidate_for_changelog=candidate,
        changelog_line=changelog_line,
        classification_comment=comment,
        confidence=confidence,
        is_merge_commit=commit.is_merge_commit,
        attention_flags=[],
    )

    _attach_attention_flags(vc)
    return vc


def _attach_attention_flags(vc: VerifiedCommit) -> None:
    """Populate ``vc.attention_flags`` according to Spec §10.2.3."""
    flags: List[str] = []

    if vc.category == LlmCategory.UNCLEAR:
        flags.append(ATTENTION_UNCLEAR)

    if vc.confidence == Confidence.LOW:
        flags.append(ATTENTION_LOW_CONFIDENCE)

    if vc.candidate_for_changelog and not vc.changelog_line:
        flags.append(ATTENTION_MISSING_CHANGELOG_LINE)

    # Author: external with no email is a proxy for missing profile info.
    # (VerifiedCommit doesn't carry github_login/github_profile_url — those
    # live on the raw Commit.  We flag the author if they are external and
    # have no email, which is the only author-identity field available here.)
    if vc.is_external and not vc.author_email:
        flags.append(ATTENTION_EXTERNAL_NO_PROFILE)

    # Co-author checks.
    # CoAuthor only has name/email/is_external — github_login is not on the
    # dataclass, so we cannot check for "UNKNOWN" here.  That check is done
    # at stage 2 and surfaced via the co-author's is_external flag.
    for ca in vc.co_authors:
        if ca.is_external and not ca.email:
            flags.append(ATTENTION_COAUTHOR_EXTERNAL_NO_PROFILE)

    if vc.is_merge_commit:
        flags.append(ATTENTION_MERGE_COMMIT)

    # LLM fallback: unclear + low confidence from LLM source.
    if (
        vc.classification_source == ClassificationSource.LLM
        and vc.category == LlmCategory.UNCLEAR
        and vc.confidence == Confidence.LOW
    ):
        flags.append(ATTENTION_LLM_FALLBACK)

    vc.attention_flags = flags


# ---------------------------------------------------------------------------
# Mandatory checks (Spec §10.1 "Mandatory checks")
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CheckResults:
    """Results of the mandatory integrity checks."""

    same_sha_set: bool = True
    same_checksum: bool = True
    no_duplicates: bool = True
    all_have_classification: bool = True
    llm_output_complete: bool = True

    @property
    def passed(self) -> bool:
        return all([
            self.same_sha_set,
            self.same_checksum,
            self.no_duplicates,
            self.all_have_classification,
            self.llm_output_complete,
        ])


def _run_checks(
    manifest: Dict[str, Any],
    commits: List[Commit],
    llm_map: Dict[str, LlmClassification],
    verified: List[VerifiedCommit],
) -> CheckResults:
    """Run all mandatory checks and return a :class:`CheckResults` object.

    Does NOT raise — callers decide whether to abort on failure.
    """
    results = CheckResults()

    # --- no_duplicates ---
    output_shas = [vc.sha for vc in verified]
    sha_counts = Counter(output_shas)
    if any(count > 1 for count in sha_counts.values()):
        results.no_duplicates = False

    # --- same_sha_set ---
    expected_set: Set[str] = set(manifest.get("sha_list", []))
    output_set: Set[str] = set(output_shas)
    if expected_set != output_set:
        results.same_sha_set = False

    # --- same_checksum ---
    expected_checksum = manifest.get("sha_checksum", "")
    actual_checksum = compute_sha_checksum(output_shas)
    if actual_checksum != expected_checksum:
        results.same_checksum = False

    # --- all_have_classification ---
    for vc in verified:
        if vc.category is None:
            results.all_have_classification = False
            break

    # --- llm_output_complete ---
    # Every commit that was sent to the LLM must have a result in llm_map.
    for commit in commits:
        ac = commit.auto_classification
        if ac is not None and ac.send_to_llm and commit.sha not in llm_map:
            results.llm_output_complete = False
            break

    return results


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def _compute_stats(
    commits: List[Commit],
    verified: List[VerifiedCommit],
) -> Dict[str, int]:
    """Compute the statistics block for the verification report."""
    from changelog_tool.models import AutoClassificationReason

    sent_to_llm = 0
    skipped_merge = 0
    skipped_docs = 0
    skipped_bugfix = 0
    skipped_size = 0
    candidates = 0
    external_contributors: Set[str] = set()

    for commit in commits:
        ac = commit.auto_classification
        if ac is None:
            continue
        if ac.send_to_llm:
            sent_to_llm += 1
        elif ac.reason == AutoClassificationReason.MERGE_COMMIT:
            skipped_merge += 1
        elif ac.reason == AutoClassificationReason.DOCS_BY_SUBJECT:
            skipped_docs += 1
        elif ac.reason == AutoClassificationReason.SMALL_OR_MEDIUM_BUGFIX:
            skipped_bugfix += 1
        elif ac.reason == AutoClassificationReason.SMALL_COMMIT:
            skipped_size += 1

        if commit.is_external:
            external_contributors.add(commit.author_email or commit.sha)
        for ca in commit.co_authors:
            if ca.is_external:
                external_contributors.add(ca.email or f"coauthor-{commit.sha}")

    for vc in verified:
        if vc.candidate_for_changelog:
            candidates += 1

    return {
        "sent_to_llm": sent_to_llm,
        "skipped_by_merge": skipped_merge,
        "skipped_by_docs": skipped_docs,
        "skipped_by_bugfix": skipped_bugfix,
        "skipped_by_size": skipped_size,
        "candidates_for_changelog": candidates,
        "external_contributors": len(external_contributors),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class VerificationResult:
    """Summary returned by :func:`run_verification`."""

    verified: List[VerifiedCommit]
    checks: CheckResults
    stats: Dict[str, int]
    manifest: Dict[str, Any]


def run_verification(
    manifest_path: str,
    preclassified_path: str,
    llm_classified_path: str,
    verified_path: str,
    report_path: str,
) -> VerificationResult:
    """Run Stage 5.1: verify and merge classifications.

    Reads the three input files, merges classifications, runs mandatory checks,
    writes ``05_verified_classification.jsonl`` and
    ``05_verification_report.json``.

    Args:
        manifest_path:       Path to ``01_commits_manifest.json``.
        preclassified_path:  Path to ``03_preclassified.jsonl``.
        llm_classified_path: Path to ``04_llm_classified.jsonl``.
        verified_path:       Path to write ``05_verified_classification.jsonl``.
        report_path:         Path to write ``05_verification_report.json``.

    Returns:
        :class:`VerificationResult` with the merged commits, check results,
        and statistics.

    Raises:
        VerificationError: If any mandatory check fails.
        OSError:           If a required input file cannot be read.
    """
    # --- Load inputs ---
    manifest: Dict[str, Any] = read_json(manifest_path)

    raw_commits = read_jsonl(preclassified_path)
    commits: List[Commit] = [commit_from_dict(r) for r in raw_commits]

    # LLM classified file may not exist if no commits were sent to the LLM.
    llm_map: Dict[str, LlmClassification] = {}
    try:
        raw_llm = read_jsonl(llm_classified_path)
        for record in raw_llm:
            sha = record["sha"]
            raw_lc = record.get("llm_classification")
            if raw_lc is not None:
                llm_map[sha] = _parse_llm_classification(raw_lc)
    except FileNotFoundError:
        pass  # No LLM results — acceptable if no commits were sent to LLM.

    # --- Merge ---
    verified: List[VerifiedCommit] = []
    for commit in commits:
        vc = _merge_commit(commit, llm_map)
        verified.append(vc)

    # --- Checks ---
    checks = _run_checks(manifest, commits, llm_map, verified)

    # --- Stats ---
    stats = _compute_stats(commits, verified)

    # --- Write outputs ---
    write_jsonl(verified_path, [to_dict(vc) for vc in verified])

    output_shas = [vc.sha for vc in verified]
    report: Dict[str, Any] = {
        "input": {
            "total_commits": manifest.get("total_commits", 0),
            "sha_checksum": manifest.get("sha_checksum", ""),
        },
        "output": {
            "total_commits": len(verified),
            "sha_checksum": compute_sha_checksum(output_shas),
        },
        "passed": checks.passed,
        "checks": {
            "same_sha_set": checks.same_sha_set,
            "same_checksum": checks.same_checksum,
            "no_duplicates": checks.no_duplicates,
            "all_have_classification": checks.all_have_classification,
            "llm_output_complete": checks.llm_output_complete,
        },
        "stats": stats,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_json_atomic(report_path, report)

    # --- Raise on failure ---
    if not checks.passed:
        _raise_verification_error(checks, manifest, verified)

    return VerificationResult(
        verified=verified,
        checks=checks,
        stats=stats,
        manifest=manifest,
    )


def _raise_verification_error(
    checks: CheckResults,
    manifest: Dict[str, Any],
    verified: List[VerifiedCommit],
) -> None:
    """Build a descriptive error message and raise :class:`VerificationError`."""
    lines: List[str] = ["Verification FAILED:"]

    if not checks.no_duplicates:
        sha_counts = Counter(vc.sha for vc in verified)
        dupes = [sha for sha, count in sha_counts.items() if count > 1]
        lines.append("  ERROR: duplicate SHA found")
        for sha in dupes:
            lines.append(f"    - {sha}")

    if not checks.same_sha_set:
        expected: Set[str] = set(manifest.get("sha_list", []))
        actual: Set[str] = {vc.sha for vc in verified}
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        lines.append("  ERROR: SHA set mismatch")
        if missing:
            lines.append("  Missing SHA:")
            for sha in missing:
                lines.append(f"    - {sha}")
        if extra:
            lines.append("  Extra SHA:")
            for sha in extra:
                lines.append(f"    - {sha}")

    if not checks.same_checksum:
        lines.append("  ERROR: SHA checksum mismatch")

    if not checks.all_have_classification:
        lines.append("  ERROR: some commits have no category assigned")

    if not checks.llm_output_complete:
        lines.append(
            "  ERROR: some commits were sent to the LLM but have no "
            "classification result — run `changelog-tool collect` to complete "
            "LLM classification"
        )

    raise VerificationError("\n".join(lines))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "VerificationError",
    "VerificationResult",
    "CheckResults",
    "run_verification",
    # Attention flag constants (used by stage 5.2 and tests)
    "ATTENTION_UNCLEAR",
    "ATTENTION_LOW_CONFIDENCE",
    "ATTENTION_MISSING_CHANGELOG_LINE",
    "ATTENTION_UNKNOWN_LOGIN",
    "ATTENTION_EXTERNAL_NO_PROFILE",
    "ATTENTION_COAUTHOR_UNKNOWN_LOGIN",
    "ATTENTION_COAUTHOR_EXTERNAL_NO_PROFILE",
    "ATTENTION_MERGE_COMMIT",
    "ATTENTION_LLM_FALLBACK",
]
