"""Heuristic pre-classification for the CHANGELOG preparation tool.

Stage 3 of the pipeline: apply five ordered rules to each commit and assign
an :class:`~changelog_tool.models.AutoClassification` result.

Rules (Spec §8.5, applied in order — first match wins):
    1. Merge commit          → category=unclear,      send_to_llm=False
    2. Docs by subject       → category=docs,         send_to_llm=False
    3. Fix/bug + small size  → category=minor_bugfix, send_to_llm=False
    4. Small size            → category=small,        send_to_llm=False
    5. Everything else       → send_to_llm=True

No I/O is performed here.  The caller is responsible for reading/writing files.

Spec references
---------------
§8.4  — AutoClassification field shape
§8.5  — rule order
§8.6  — rule 1: merge commit
§8.7  — rule 2: docs by subject
§8.8  — rule 3: fix/bug by subject and size
§8.9  — rule 4: small size
§8.10 — rule 5: send to LLM
§8.11 — invariants
"""

from __future__ import annotations

import re
from typing import List

from changelog_tool.models import (
    AutoClassification,
    AutoClassificationReason,
    Commit,
    Confidence,
    LlmCategory,
)

# ---------------------------------------------------------------------------
# Compiled regexes (Spec §8.7, §8.8)
# ---------------------------------------------------------------------------

_DOCS_RE = re.compile(
    r"\b(doc|docs|documentation|readme|changelog)\b",
    re.IGNORECASE,
)

_BUGFIX_RE = re.compile(
    r"\b(fix|bug|bugfix|hotfix)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def preclassify(
    commits: List[Commit],
    small_commit_threshold: int,
    bugfix_skip_threshold: int,
) -> List[Commit]:
    """Apply heuristic pre-classification to each commit in-place.

    Sets ``commit.auto_classification`` on every commit.

    Args:
        commits:                List of commits (stage-2 output).
        small_commit_threshold: ``thresholds.small_commit`` from config.
        bugfix_skip_threshold:  ``thresholds.bugfix_skip`` from config.

    Returns:
        The same list (mutated in-place) for convenience.
    """
    for commit in commits:
        commit.auto_classification = _classify(
            commit, small_commit_threshold, bugfix_skip_threshold
        )
    return commits


# ---------------------------------------------------------------------------
# Internal classification logic
# ---------------------------------------------------------------------------


def _classify(
    commit: Commit,
    small_threshold: int,
    bugfix_threshold: int,
) -> AutoClassification:
    """Return the :class:`AutoClassification` for a single commit."""

    # Rule 1: merge commit (Spec §8.6)
    if commit.is_merge_commit:
        return AutoClassification(
            send_to_llm=False,
            reason=AutoClassificationReason.MERGE_COMMIT,
            category=LlmCategory.UNCLEAR,
            candidate_for_changelog=False,
            changelog_line=None,
            confidence=Confidence.LOW,
        )

    # Rule 2: docs by subject (Spec §8.7)
    if _DOCS_RE.search(commit.subject):
        return AutoClassification(
            send_to_llm=False,
            reason=AutoClassificationReason.DOCS_BY_SUBJECT,
            category=LlmCategory.DOCS,
            candidate_for_changelog=False,
            changelog_line=None,
            confidence=Confidence.MEDIUM,
        )

    # Rule 3: fix/bug by subject and size (Spec §8.8)
    if _BUGFIX_RE.search(commit.subject) and commit.size_score <= bugfix_threshold:
        return AutoClassification(
            send_to_llm=False,
            reason=AutoClassificationReason.SMALL_OR_MEDIUM_BUGFIX,
            category=LlmCategory.MINOR_BUGFIX,
            candidate_for_changelog=False,
            changelog_line=None,
            confidence=Confidence.MEDIUM,
        )

    # Rule 4: small size (Spec §8.9)
    if commit.size_score <= small_threshold:
        return AutoClassification(
            send_to_llm=False,
            reason=AutoClassificationReason.SMALL_COMMIT,
            category=LlmCategory.SMALL,
            candidate_for_changelog=False,
            changelog_line=None,
            confidence=Confidence.HIGH,
        )

    # Rule 5: send to LLM (Spec §8.10)
    return AutoClassification(
        send_to_llm=True,
        reason=AutoClassificationReason.REQUIRES_LLM_ANALYSIS,
        category=None,
        candidate_for_changelog=None,
        changelog_line=None,
        confidence=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = ["preclassify"]
