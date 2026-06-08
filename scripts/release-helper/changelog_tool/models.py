"""Data models and pure helper functions for the CHANGELOG preparation tool.

This module defines all shared dataclasses, enums, and pure (no-I/O) helpers
used across the entire pipeline (stages 1–7). It has no dependencies outside
the Python standard library and must not import from any other changelog_tool
module.

Spec references:
    §5   — Commit, CoAuthor, size_score, size_bucket
    §8.4 — AutoClassification
    §9.4 — LlmClassification
    §10.1 — VerifiedCommit
    §11.5 — ChangelogItem
    §11.6 — category → CHANGELOG section mapping
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SizeBucket(str, enum.Enum):
    """Commit size bucket (Spec §5.3).

    Inherits ``str`` so that ``json.dumps`` serialises the value directly
    without a custom encoder.
    """

    TINY = "tiny"      # size_score <= 10
    SMALL = "small"    # size_score <= 50
    MEDIUM = "medium"  # size_score <= 200
    LARGE = "large"    # size_score <= 1000
    HUGE = "huge"      # size_score > 1000


class AutoClassificationReason(str, enum.Enum):
    """Reason assigned by the heuristic pre-classification stage (Spec §8.5)."""

    MERGE_COMMIT = "merge_commit"
    DOCS_BY_SUBJECT = "docs_by_subject"
    SMALL_OR_MEDIUM_BUGFIX = "small_or_medium_bugfix"
    SMALL_COMMIT = "small_commit"
    REQUIRES_LLM_ANALYSIS = "requires_llm_analysis"


class LlmCategory(str, enum.Enum):
    """Commit category (Spec §9.5).

    The values ``SMALL`` and ``MINOR_BUGFIX`` are assigned only by the
    heuristic pre-classification stage and are never returned by the LLM.
    """

    FEATURE = "feature"
    IMPROVEMENT = "improvement"
    BREAKING_CHANGE = "breaking_change"
    IMPORTANT_BUGFIX = "important_bugfix"
    INTERNAL = "internal"
    DOCS = "docs"
    TESTS = "tests"
    CHORE = "chore"
    UNCLEAR = "unclear"
    # Pre-classification only — not returned by the LLM:
    SMALL = "small"
    MINOR_BUGFIX = "minor_bugfix"


class ClassificationSource(str, enum.Enum):
    """Which stage produced the final classification for a commit."""

    AUTO = "auto"
    LLM = "llm"


class Confidence(str, enum.Enum):
    """Confidence level of a classification result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ChangelogSection(str, enum.Enum):
    """Section headings used in the proposed CHANGELOG (Spec §11.6)."""

    BREAKING_CHANGES = "Breaking changes"
    FUNCTIONALITY = "Functionality"
    IMPROVEMENTS = "Improvements"
    BUG_FIXES = "Bug fixes"
    DOCUMENTATION = "Documentation"
    OTHER = "Other"


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

_CATEGORY_TO_SECTION: Dict[LlmCategory, ChangelogSection] = {
    LlmCategory.BREAKING_CHANGE: ChangelogSection.BREAKING_CHANGES,
    LlmCategory.FEATURE: ChangelogSection.FUNCTIONALITY,
    LlmCategory.IMPROVEMENT: ChangelogSection.IMPROVEMENTS,
    LlmCategory.IMPORTANT_BUGFIX: ChangelogSection.BUG_FIXES,
    LlmCategory.DOCS: ChangelogSection.DOCUMENTATION,
}


def compute_size_score(insertions: int, deletions: int) -> int:
    """Return the size score for a commit (Spec §5.2).

    ``size_score = insertions + deletions``
    """
    return insertions + deletions


def compute_size_bucket(size_score: int) -> SizeBucket:
    """Map a size score to a :class:`SizeBucket` (Spec §5.3).

    Boundary values:
        tiny   <= 10
        small  <= 50
        medium <= 200
        large  <= 1000
        huge   > 1000
    """
    if size_score <= 10:
        return SizeBucket.TINY
    if size_score <= 50:
        return SizeBucket.SMALL
    if size_score <= 200:
        return SizeBucket.MEDIUM
    if size_score <= 1000:
        return SizeBucket.LARGE
    return SizeBucket.HUGE


def category_to_section(category: LlmCategory) -> Optional[ChangelogSection]:
    """Map a category to its CHANGELOG section (Spec §11.6).

    Returns ``None`` for categories that are not included in the CHANGELOG by
    default (``internal``, ``tests``, ``chore``, ``unclear``, ``small``,
    ``minor_bugfix``).
    """
    return _CATEGORY_TO_SECTION.get(category)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CoAuthor:
    """A co-author extracted from a ``Co-authored-by:`` trailer (Spec §5.5)."""

    name: str
    email: str
    is_external: Optional[bool] = None  # filled at stage 2


@dataclasses.dataclass
class AutoClassification:
    """Result of the heuristic pre-classification stage (Spec §8.4).

    When ``send_to_llm`` is ``True``, the fields ``category``,
    ``candidate_for_changelog``, and ``confidence`` are ``None`` because the
    LLM has not yet run.
    """

    send_to_llm: bool
    reason: AutoClassificationReason
    category: Optional[LlmCategory]          # None when send_to_llm=True
    candidate_for_changelog: Optional[bool]  # None when send_to_llm=True
    changelog_line: Optional[str]
    confidence: Optional[Confidence]         # None when send_to_llm=True


@dataclasses.dataclass
class LlmClassification:
    """Result returned by the LLM for a single commit (Spec §9.4)."""

    category: LlmCategory
    candidate_for_changelog: bool
    changelog_line: Optional[str]
    comment: Optional[str]
    confidence: Confidence


@dataclasses.dataclass
class Commit:
    """Full representation of a single commit (Spec §5.1).

    Fields are grouped by the pipeline stage that populates them.
    Stage-1 fields are always present after ``collect`` runs.
    Stage-2 fields (GitHub) are ``None`` until the contributor-resolution
    stage fills them.  Stage-3 and stage-4 fields are ``None`` until the
    corresponding stages run.
    """

    # --- Stage 1: collected from git ---
    sha: str
    short_sha: str
    is_merge_commit: bool

    author_name: str
    author_email: str
    co_authors: List[CoAuthor]

    subject: str
    body: str
    message: str
    commit_url: str

    changed_files: List[str]
    insertions: int
    deletions: int
    files_count: int
    size_score: int
    size_bucket: SizeBucket

    # --- Stage 2: contributor resolution ---
    is_external: Optional[bool] = None

    # --- Stage 3: heuristic pre-classification ---
    auto_classification: Optional[AutoClassification] = None

    # --- Stage 4: LLM classification ---
    llm_classification: Optional[LlmClassification] = None


@dataclasses.dataclass
class VerifiedCommit:
    """Unified commit object produced by stage 5.1 (Spec §10.1).

    Kept as a separate type from :class:`Commit` so that the type system
    enforces that verification has run before downstream stages (6, 7) operate
    on the data.
    """

    sha: str
    short_sha: str

    subject: str
    body: str
    commit_url: str

    author_name: str
    author_email: str
    is_external: bool
    co_authors: List[CoAuthor]

    changed_files: List[str]
    insertions: int
    deletions: int
    files_count: int
    size_score: int
    size_bucket: SizeBucket

    classification_source: ClassificationSource
    category: LlmCategory
    candidate_for_changelog: bool
    changelog_line: Optional[str]
    classification_comment: Optional[str]
    confidence: Confidence

    is_merge_commit: bool = False
    # Populated by stage 5.2 attention-flag logic:
    attention_flags: List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ChangelogItem:
    """A single item proposed for inclusion in the CHANGELOG (Spec §11.5)."""

    sha: str
    short_sha: str
    category: LlmCategory
    changelog_section: ChangelogSection
    changelog_line: str
    author_name: str
    author_email: str
    co_authors: List[CoAuthor]
    is_external: bool
    commit_url: str
    source: ClassificationSource
    maintainer_comment: Optional[str] = None
    attention_flags: List[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses and enums to JSON-serialisable types.

    - Dataclass instances → ``dict`` (field name → converted value).
    - ``enum.Enum`` instances → their ``.value``.
    - ``list`` → list with each element converted.
    - Everything else is returned as-is (``str``, ``int``, ``bool``, ``None``).

    Usage::

        import json
        json.dumps(to_dict(commit))
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]
    return obj


def _co_author_from_dict(d: Dict[str, Any]) -> CoAuthor:
    return CoAuthor(
        name=d["name"],
        email=d["email"],
        is_external=d.get("is_external"),
    )


def _auto_classification_from_dict(d: Dict[str, Any]) -> AutoClassification:
    raw_category = d.get("category")
    raw_confidence = d.get("confidence")
    return AutoClassification(
        send_to_llm=d["send_to_llm"],
        reason=AutoClassificationReason(d["reason"]),
        category=LlmCategory(raw_category) if raw_category is not None else None,
        candidate_for_changelog=d.get("candidate_for_changelog"),
        changelog_line=d.get("changelog_line"),
        confidence=Confidence(raw_confidence) if raw_confidence is not None else None,
    )


def _llm_classification_from_dict(d: Dict[str, Any]) -> LlmClassification:
    return LlmClassification(
        category=LlmCategory(d["category"]),
        candidate_for_changelog=d["candidate_for_changelog"],
        changelog_line=d.get("changelog_line"),
        comment=d.get("comment"),
        confidence=Confidence(d["confidence"]),
    )


def commit_from_dict(d: Dict[str, Any]) -> Commit:
    """Reconstruct a :class:`Commit` from a plain dict (e.g. parsed from JSONL)."""
    raw_ac = d.get("auto_classification")
    raw_lc = d.get("llm_classification")
    return Commit(
        sha=d["sha"],
        short_sha=d["short_sha"],
        is_merge_commit=d["is_merge_commit"],
        author_name=d["author_name"],
        author_email=d["author_email"],
        co_authors=[_co_author_from_dict(c) for c in d.get("co_authors", [])],
        subject=d["subject"],
        body=d["body"],
        message=d["message"],
        commit_url=d["commit_url"],
        changed_files=d.get("changed_files", []),
        insertions=d["insertions"],
        deletions=d["deletions"],
        files_count=d["files_count"],
        size_score=d["size_score"],
        size_bucket=SizeBucket(d["size_bucket"]),
        is_external=d.get("is_external"),
        auto_classification=(
            _auto_classification_from_dict(raw_ac) if raw_ac is not None else None
        ),
        llm_classification=(
            _llm_classification_from_dict(raw_lc) if raw_lc is not None else None
        ),
    )


def verified_commit_from_dict(d: Dict[str, Any]) -> VerifiedCommit:
    """Reconstruct a :class:`VerifiedCommit` from a plain dict."""
    return VerifiedCommit(
        sha=d["sha"],
        short_sha=d["short_sha"],
        subject=d["subject"],
        body=d["body"],
        commit_url=d["commit_url"],
        author_name=d["author_name"],
        author_email=d["author_email"],
        is_external=d["is_external"],
        co_authors=[_co_author_from_dict(c) for c in d.get("co_authors", [])],
        changed_files=d.get("changed_files", []),
        insertions=d["insertions"],
        deletions=d["deletions"],
        files_count=d["files_count"],
        size_score=d["size_score"],
        size_bucket=SizeBucket(d["size_bucket"]),
        classification_source=ClassificationSource(d["classification_source"]),
        category=LlmCategory(d["category"]),
        candidate_for_changelog=d["candidate_for_changelog"],
        changelog_line=d.get("changelog_line"),
        classification_comment=d.get("classification_comment"),
        confidence=Confidence(d["confidence"]),
        is_merge_commit=d.get("is_merge_commit", False),
        attention_flags=d.get("attention_flags", []),
    )


def changelog_item_from_dict(d: Dict[str, Any]) -> ChangelogItem:
    """Reconstruct a :class:`ChangelogItem` from a plain dict."""
    return ChangelogItem(
        sha=d["sha"],
        short_sha=d["short_sha"],
        category=LlmCategory(d["category"]),
        changelog_section=ChangelogSection(d["changelog_section"]),
        changelog_line=d["changelog_line"],
        author_name=d.get("author_name", ""),
        author_email=d.get("author_email", ""),
        co_authors=[_co_author_from_dict(c) for c in d.get("co_authors", [])],
        is_external=d["is_external"],
        commit_url=d["commit_url"],
        source=ClassificationSource(d["source"]),
        maintainer_comment=d.get("maintainer_comment"),
        attention_flags=d.get("attention_flags", []),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "SizeBucket",
    "AutoClassificationReason",
    "LlmCategory",
    "ClassificationSource",
    "Confidence",
    "ChangelogSection",
    # Pure helpers
    "compute_size_score",
    "compute_size_bucket",
    "category_to_section",
    # Dataclasses
    "CoAuthor",
    "AutoClassification",
    "LlmClassification",
    "Commit",
    "VerifiedCommit",
    "ChangelogItem",
    # Serialisation
    "to_dict",
    "commit_from_dict",
    "verified_commit_from_dict",
    "changelog_item_from_dict",
]
