"""Tests for changelog_tool.llm.parsing (offline, no network).

Covers:
- extract_json_block: fenced and unfenced input
- parse_classification_response: valid, invariant violations, fallback coercion
- parse_missing_line_response: plain text, fenced, JSON-wrapped, empty
"""

from __future__ import annotations

import json
import pytest

from changelog_tool.llm.errors import LlmResponseError
from changelog_tool.llm.parsing import (
    extract_json_block,
    parse_classification_response,
    parse_missing_line_response,
)


# ---------------------------------------------------------------------------
# extract_json_block
# ---------------------------------------------------------------------------


class TestExtractJsonBlock:
    def test_json_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert extract_json_block(text) == '{"key": "value"}'

    def test_plain_fence(self):
        text = '```\n{"key": "value"}\n```'
        assert extract_json_block(text) == '{"key": "value"}'

    def test_no_fence_returns_stripped(self):
        text = '  {"key": "value"}  '
        assert extract_json_block(text) == '{"key": "value"}'

    def test_fence_with_extra_text_before(self):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        assert extract_json_block(text) == '{"a": 1}'

    def test_case_insensitive_fence(self):
        text = '```JSON\n{"x": 1}\n```'
        assert extract_json_block(text) == '{"x": 1}'

    def test_empty_string(self):
        assert extract_json_block("") == ""


# ---------------------------------------------------------------------------
# parse_classification_response
# ---------------------------------------------------------------------------


def _make_valid_response(sha_list):
    """Build a valid classification response JSON string."""
    items = [
        {
            "sha": sha,
            "category": "feature",
            "candidate_for_changelog": True,
            "changelog_line": f"Added something for {sha[:8]}.",
            "comment": "Test comment.",
            "confidence": "high",
        }
        for sha in sha_list
    ]
    return json.dumps({
        "items": items,
        "stats": {"input_count": len(sha_list), "output_count": len(items)},
    })


SHA1 = "a" * 40
SHA2 = "b" * 40
SHA3 = "c" * 40


class TestParseClassificationResponse:
    def test_valid_single_sha(self):
        text = _make_valid_response([SHA1])
        results = parse_classification_response(text, [SHA1])
        assert len(results) == 1
        assert results[0].category.value == "feature"
        assert results[0].candidate_for_changelog is True
        assert results[0].confidence.value == "high"

    def test_valid_multiple_shas(self):
        text = _make_valid_response([SHA1, SHA2, SHA3])
        results = parse_classification_response(text, [SHA1, SHA2, SHA3])
        assert len(results) == 3
        # Order must match expected_shas
        assert results[0].changelog_line is not None
        assert results[1].changelog_line is not None

    def test_result_order_matches_expected_shas(self):
        """Results must be in the same order as expected_shas, not items order."""
        items = [
            {"sha": SHA2, "category": "internal", "candidate_for_changelog": False,
             "changelog_line": None, "comment": None, "confidence": "high"},
            {"sha": SHA1, "category": "feature", "candidate_for_changelog": True,
             "changelog_line": "Added X.", "comment": None, "confidence": "high"},
        ]
        text = json.dumps({
            "items": items,
            "stats": {"input_count": 2, "output_count": 2},
        })
        results = parse_classification_response(text, [SHA1, SHA2])
        assert results[0].category.value == "feature"   # SHA1 first
        assert results[1].category.value == "internal"  # SHA2 second

    def test_fenced_json_is_accepted(self):
        inner = _make_valid_response([SHA1])
        text = f"```json\n{inner}\n```"
        results = parse_classification_response(text, [SHA1])
        assert len(results) == 1

    def test_invalid_json_raises(self):
        with pytest.raises(LlmResponseError, match="not valid JSON"):
            parse_classification_response("not json at all", [SHA1])

    def test_missing_items_key_raises(self):
        text = json.dumps({"stats": {"input_count": 1, "output_count": 0}})
        with pytest.raises(LlmResponseError, match="missing 'items'"):
            parse_classification_response(text, [SHA1])

    def test_stats_input_count_mismatch_raises(self):
        items = [{"sha": SHA1, "category": "feature", "candidate_for_changelog": True,
                  "changelog_line": None, "comment": None, "confidence": "high"}]
        text = json.dumps({
            "items": items,
            "stats": {"input_count": 99, "output_count": 1},
        })
        with pytest.raises(LlmResponseError, match="input_count"):
            parse_classification_response(text, [SHA1])

    def test_stats_output_count_mismatch_raises(self):
        items = [{"sha": SHA1, "category": "feature", "candidate_for_changelog": True,
                  "changelog_line": None, "comment": None, "confidence": "high"}]
        text = json.dumps({
            "items": items,
            "stats": {"input_count": 1, "output_count": 99},
        })
        with pytest.raises(LlmResponseError, match="output_count"):
            parse_classification_response(text, [SHA1])

    def test_duplicate_sha_raises(self):
        items = [
            {"sha": SHA1, "category": "feature", "candidate_for_changelog": True,
             "changelog_line": None, "comment": None, "confidence": "high"},
            {"sha": SHA1, "category": "internal", "candidate_for_changelog": False,
             "changelog_line": None, "comment": None, "confidence": "high"},
        ]
        text = json.dumps({
            "items": items,
            "stats": {"input_count": 1, "output_count": 2},
        })
        with pytest.raises(LlmResponseError, match="[Dd]uplicate"):
            parse_classification_response(text, [SHA1])

    def test_unknown_sha_raises(self):
        items = [{"sha": SHA2, "category": "feature", "candidate_for_changelog": True,
                  "changelog_line": None, "comment": None, "confidence": "high"}]
        text = json.dumps({
            "items": items,
            "stats": {"input_count": 1, "output_count": 1},
        })
        with pytest.raises(LlmResponseError, match="unexpected SHA"):
            parse_classification_response(text, [SHA1])

    def test_missing_sha_raises(self):
        text = json.dumps({
            "items": [],
            "stats": {"input_count": 1, "output_count": 0},
        })
        with pytest.raises(LlmResponseError):
            parse_classification_response(text, [SHA1])

    def test_unknown_category_coerced_to_unclear(self):
        items = [{"sha": SHA1, "category": "TOTALLY_UNKNOWN", "candidate_for_changelog": False,
                  "changelog_line": None, "comment": None, "confidence": "high"}]
        text = json.dumps({
            "items": items,
            "stats": {"input_count": 1, "output_count": 1},
        })
        results = parse_classification_response(text, [SHA1])
        assert results[0].category.value == "unclear"

    def test_unknown_confidence_coerced_to_low(self):
        items = [{"sha": SHA1, "category": "feature", "candidate_for_changelog": True,
                  "changelog_line": "X.", "comment": None, "confidence": "UNKNOWN"}]
        text = json.dumps({
            "items": items,
            "stats": {"input_count": 1, "output_count": 1},
        })
        results = parse_classification_response(text, [SHA1])
        assert results[0].confidence.value == "low"

    def test_missing_stats_is_tolerated(self):
        """stats is optional — missing it should not raise."""
        items = [{"sha": SHA1, "category": "chore", "candidate_for_changelog": False,
                  "changelog_line": None, "comment": None, "confidence": "medium"}]
        text = json.dumps({"items": items})
        results = parse_classification_response(text, [SHA1])
        assert len(results) == 1

    def test_root_not_dict_raises(self):
        text = json.dumps([1, 2, 3])
        with pytest.raises(LlmResponseError, match="JSON object"):
            parse_classification_response(text, [SHA1])


# ---------------------------------------------------------------------------
# parse_missing_line_response
# ---------------------------------------------------------------------------


class TestParseMissingLineResponse:
    def test_plain_text(self):
        assert parse_missing_line_response("Added Redis pipelining support.") == \
               "Added Redis pipelining support."

    def test_strips_leading_whitespace(self):
        assert parse_missing_line_response("  Added X.  ") == "Added X."

    def test_strips_markdown_bullet(self):
        assert parse_missing_line_response("- Added X.") == "Added X."

    def test_strips_asterisk_bullet(self):
        assert parse_missing_line_response("* Added X.") == "Added X."

    def test_fenced_plain_text(self):
        text = "```\nAdded X.\n```"
        assert parse_missing_line_response(text) == "Added X."

    def test_json_string_value(self):
        text = json.dumps("Added X.")
        assert parse_missing_line_response(text) == "Added X."

    def test_json_object_changelog_line_key(self):
        text = json.dumps({"changelog_line": "Added X."})
        assert parse_missing_line_response(text) == "Added X."

    def test_json_object_line_key(self):
        text = json.dumps({"line": "Added X."})
        assert parse_missing_line_response(text) == "Added X."

    def test_takes_first_nonempty_line(self):
        text = "\n\nAdded X.\nIgnored second line."
        assert parse_missing_line_response(text) == "Added X."

    def test_empty_raises(self):
        with pytest.raises(LlmResponseError, match="empty"):
            parse_missing_line_response("")

    def test_whitespace_only_raises(self):
        with pytest.raises(LlmResponseError, match="empty"):
            parse_missing_line_response("   \n  ")
