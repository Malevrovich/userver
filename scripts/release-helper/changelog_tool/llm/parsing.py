"""Tolerant LLM response parsing for the CHANGELOG preparation tool.

Implements the two-step cleanup described in Spec §9.10:

1. Try ``json.loads`` directly.
2. If that fails, strip Markdown code fences and retry.
3. If still invalid, raise :class:`~changelog_tool.llm.errors.LlmResponseError`
   so the caller (T7/T10) can retry once then fall back to ``unclear``.

Also enforces the §9.12 batch invariants after successful JSON parsing:

- ``stats.input_count`` == number of expected SHAs.
- ``stats.output_count`` == number of items.
- Each expected SHA returned exactly once.
- No unknown SHAs in the response.

Spec references
---------------
§9.10 — handling invalid JSON
§9.12 — per-batch invariants
§11.8 — missing-line response cleanup
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set

from changelog_tool.llm.errors import LlmResponseError
from changelog_tool.models import Confidence, LlmCategory, LlmClassification

# ---------------------------------------------------------------------------
# JSON extraction helpers (Spec §9.10)
# ---------------------------------------------------------------------------

# Matches ```json ... ``` or ``` ... ``` fences (non-greedy, DOTALL).
_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def extract_json_block(text: str) -> str:
    """Extract JSON from a Markdown code fence, or return *text* unchanged.

    Supported variants (Spec §9.10)::

        ```json
        { ... }
        ```

        ```
        { ... }
        ```

    If no fence is found, the original *text* is returned so the caller can
    attempt ``json.loads`` on it directly.

    Args:
        text: Raw model output.

    Returns:
        The content inside the first code fence, or *text* if no fence found.
    """
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _try_parse_json(text: str) -> Any:
    """Attempt to parse *text* as JSON, applying fence extraction on failure.

    Returns the parsed object, or raises :class:`LlmResponseError` if both
    attempts fail (Spec §9.10).
    """
    # First attempt: direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Second attempt: strip Markdown fences and retry.
    cleaned = extract_json_block(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LlmResponseError(
            f"LLM response is not valid JSON after Markdown cleanup: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Classification response parsing (Spec §9.12)
# ---------------------------------------------------------------------------

_VALID_CATEGORIES: Set[str] = {c.value for c in LlmCategory}
_VALID_CONFIDENCES: Set[str] = {c.value for c in Confidence}


def _coerce_category(raw: Any) -> LlmCategory:
    """Coerce a raw category value to :class:`~changelog_tool.models.LlmCategory`.

    Falls back to ``unclear`` for unknown values rather than raising, so a
    single bad category does not invalidate the whole batch.
    """
    if isinstance(raw, str) and raw in _VALID_CATEGORIES:
        return LlmCategory(raw)
    return LlmCategory.UNCLEAR


def _coerce_confidence(raw: Any) -> Confidence:
    """Coerce a raw confidence value to :class:`~changelog_tool.models.Confidence`.

    Falls back to ``low`` for unknown values.
    """
    if isinstance(raw, str) and raw in _VALID_CONFIDENCES:
        return Confidence(raw)
    return Confidence.LOW


def parse_classification_response(
    text: str,
    expected_shas: List[str],
) -> List[LlmClassification]:
    """Parse and validate a batch-classification LLM response (Spec §9.12).

    Steps:
    1. Parse JSON (with Markdown fence cleanup per §9.10).
    2. Enforce §9.12 invariants:
       - ``stats.input_count == len(expected_shas)``
       - ``stats.output_count == len(items)``
       - Each expected SHA returned exactly once.
       - No unknown SHAs.
    3. Coerce each item into :class:`~changelog_tool.models.LlmClassification`.

    Args:
        text:          Raw model output text.
        expected_shas: The list of SHAs that were sent in this batch.

    Returns:
        List of :class:`~changelog_tool.models.LlmClassification` objects in
        the same order as *expected_shas*.

    Raises:
        LlmResponseError: JSON is invalid, or §9.12 invariants are violated.
    """
    data = _try_parse_json(text)

    if not isinstance(data, dict):
        raise LlmResponseError(
            f"LLM response root must be a JSON object, got {type(data).__name__}"
        )

    items_raw = data.get("items")
    if not isinstance(items_raw, list):
        raise LlmResponseError(
            "LLM response missing 'items' array or 'items' is not a list"
        )

    stats = data.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}

    expected_count = len(expected_shas)
    input_count = stats.get("input_count")
    output_count = stats.get("output_count")

    # §9.12: stats.input_count must match batch size.
    if input_count is not None and input_count != expected_count:
        raise LlmResponseError(
            f"stats.input_count={input_count} does not match "
            f"expected batch size={expected_count}"
        )

    # §9.12: stats.output_count must match items length.
    if output_count is not None and output_count != len(items_raw):
        raise LlmResponseError(
            f"stats.output_count={output_count} does not match "
            f"actual items count={len(items_raw)}"
        )

    # Build a SHA → item mapping and check for duplicates / unknown SHAs.
    expected_set = set(expected_shas)
    seen: Dict[str, Any] = {}
    for item in items_raw:
        if not isinstance(item, dict):
            raise LlmResponseError(
                f"Each item in 'items' must be a JSON object, got {type(item).__name__}"
            )
        sha = item.get("sha")
        if not isinstance(sha, str) or not sha:
            raise LlmResponseError(
                f"Item missing or invalid 'sha' field: {item!r}"
            )
        if sha in seen:
            raise LlmResponseError(
                f"Duplicate SHA in LLM response: {sha}"
            )
        if sha not in expected_set:
            raise LlmResponseError(
                f"LLM returned unexpected SHA not in batch: {sha}"
            )
        seen[sha] = item

    # §9.12: every expected SHA must be present.
    missing = expected_set - set(seen.keys())
    if missing:
        raise LlmResponseError(
            f"LLM response missing SHAs: {sorted(missing)}"
        )

    # Build result list in the same order as expected_shas.
    result: List[LlmClassification] = []
    for sha in expected_shas:
        item = seen[sha]
        result.append(
            LlmClassification(
                category=_coerce_category(item.get("category")),
                candidate_for_changelog=bool(item.get("candidate_for_changelog", False)),
                changelog_line=item.get("changelog_line") or None,
                comment=item.get("comment") or None,
                confidence=_coerce_confidence(item.get("confidence")),
            )
        )

    return result


# ---------------------------------------------------------------------------
# Missing-line response parsing (Spec §11.8)
# ---------------------------------------------------------------------------


def parse_missing_line_response(text: str) -> str:
    """Extract a single CHANGELOG line from a missing-line LLM response.

    The model is instructed to return plain text (Spec §11.8), but may wrap
    the line in Markdown or JSON.  This function strips common wrappers and
    returns the first non-empty line.

    Args:
        text: Raw model output.

    Returns:
        A single non-empty CHANGELOG line.

    Raises:
        LlmResponseError: The response is empty after cleanup.
    """
    cleaned = text.strip()

    # Try to extract from a Markdown fence first.
    fence_content = extract_json_block(cleaned)
    if fence_content != cleaned:
        cleaned = fence_content.strip()

    # If it looks like JSON, try to extract a string value.
    if cleaned.startswith("{") or cleaned.startswith("[") or cleaned.startswith('"'):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, str):
                cleaned = parsed.strip()
            elif isinstance(parsed, dict):
                # Accept common keys the model might use.
                for key in ("changelog_line", "line", "text", "result"):
                    val = parsed.get(key)
                    if isinstance(val, str) and val.strip():
                        cleaned = val.strip()
                        break
        except json.JSONDecodeError:
            pass

    # Strip leading Markdown bullet characters.
    cleaned = re.sub(r"^[\-\*\+]\s+", "", cleaned)

    # Take only the first non-empty line.
    for line in cleaned.splitlines():
        line = line.strip()
        if line:
            return line

    raise LlmResponseError(
        "LLM returned an empty response for missing CHANGELOG line generation"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "extract_json_block",
    "parse_classification_response",
    "parse_missing_line_response",
]
