"""Prompt builders for the LLM layer.

Implements the two prompt contracts from the specification:

- :func:`build_classification_prompt` — Spec §9.11 (batch classification).
- :func:`build_missing_line_prompt`   — Spec §11.8 (single missing CHANGELOG line).

Also provides :func:`estimate_chars` so T7 can enforce ``llm.max_prompt_chars``
and split batches between commits (Spec §9.7).

All prompt text is kept in this module so it is easy to review and update
without touching pipeline logic.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from changelog_tool.llm.base import LlmRequest

# ---------------------------------------------------------------------------
# Classification prompt (Spec §9.11)
# ---------------------------------------------------------------------------

_CLASSIFICATION_SYSTEM = """\
You help prepare a CHANGELOG for a C++ framework/library.

You are given a list of commits between releases. For each commit, decide:
1. Whether it should be included in the CHANGELOG.
2. Which category it belongs to.
3. If it should be included, write a short CHANGELOG line in English.
4. Provide a short comment for the maintainer.

Important:
- Do not invent facts that are not present in the commit subject/body/files.
- If unsure, use category = "unclear".
- Every input SHA must be returned exactly once.
- Do not skip commits.
- Do not add SHA values that were not present in the input.

Categories:
- feature
- improvement
- breaking_change
- important_bugfix
- internal
- docs
- tests
- chore
- unclear

Rules for inclusion in CHANGELOG:
Include the commit if it adds public functionality, changes behavior,
adds a noticeable improvement, contains a breaking change, an important bugfix,
or an important build/platform change.

Do not include it if it is only internal refactoring, tests, CI, formatting,
minor docs, or cleanup.

Response format — strict JSON:

{
  "items": [
    {
      "sha": "...",
      "category": "feature",
      "candidate_for_changelog": true,
      "changelog_line": "Added support for ...",
      "comment": "Why this was classified this way.",
      "confidence": "high"
    }
  ],
  "stats": {
    "input_count": 0,
    "output_count": 0
  }
}

Commits:
"""

# ---------------------------------------------------------------------------
# Missing-line prompt (Spec §11.8)
# ---------------------------------------------------------------------------

_MISSING_LINE_SYSTEM = """\
You help prepare a CHANGELOG for a C++ framework/library.

You are given information about a single commit that has been selected for
inclusion in the CHANGELOG, but no CHANGELOG line has been written yet.

Write exactly one short CHANGELOG line in English describing what this commit
does from a user's perspective.

Requirements:
- One line only.
- No Markdown bullets, no links, no invented facts.
- Start with a capital letter.
- Do not wrap the line in quotes, JSON, or Markdown code blocks.
- If you cannot determine a meaningful line, respond with the commit subject
  verbatim (do not add any prefix or suffix).
"""


def build_classification_prompt(
    contexts: List[Dict[str, Any]],
) -> LlmRequest:
    """Build the batch-classification prompt (Spec §9.11).

    Args:
        contexts: List of per-commit context dicts produced by
                  :func:`~changelog_tool.llm.context.commit_to_context`.
                  Each dict must contain at least ``"sha"``.

    Returns:
        An :class:`~changelog_tool.llm.base.LlmRequest` ready to send.
    """
    commits_json = json.dumps(contexts, ensure_ascii=False, indent=2)
    user_text = commits_json

    sha_list = [c.get("sha", "") for c in contexts]
    return LlmRequest(
        system=_CLASSIFICATION_SYSTEM,
        user=user_text,
        metadata={"sha_list": sha_list, "batch_size": len(contexts)},
    )


def build_missing_line_prompt(
    context: Dict[str, Any],
    category: str,
    maintainer_comment: Optional[str] = None,
) -> LlmRequest:
    """Build the single-commit missing-line prompt (Spec §11.8).

    Args:
        context:            Per-commit context dict (from
                            :func:`~changelog_tool.llm.context.commit_to_context`).
        category:           The LLM or override category for this commit.
        maintainer_comment: Optional free-text note from the maintainer.

    Returns:
        An :class:`~changelog_tool.llm.base.LlmRequest` ready to send.
    """
    parts: List[str] = [
        f"Subject: {context.get('subject', '')}",
        f"Body: {context.get('body', '') or '(none)'}",
        f"Changed files: {', '.join(context.get('changed_files', [])) or '(none)'}",
        f"Category: {category}",
    ]
    if maintainer_comment:
        parts.append(f"Maintainer note: {maintainer_comment}")

    user_text = "\n".join(parts)
    return LlmRequest(
        system=_MISSING_LINE_SYSTEM,
        user=user_text,
        metadata={"sha": context.get("sha", ""), "category": category},
    )


# ---------------------------------------------------------------------------
# Size estimation (Spec §9.7)
# ---------------------------------------------------------------------------


def estimate_chars(request: LlmRequest) -> int:
    """Estimate the total character count of a prompt.

    Used by T7 to enforce ``llm.max_prompt_chars`` and split batches between
    commits (Spec §9.7).  The estimate is intentionally conservative
    (system + user lengths summed) — it does not account for tokenisation.

    Args:
        request: The prompt to measure.

    Returns:
        Approximate character count (``len(system) + len(user)``).
    """
    return len(request.system) + len(request.user)


__all__ = [
    "build_classification_prompt",
    "build_missing_line_prompt",
    "estimate_chars",
]
