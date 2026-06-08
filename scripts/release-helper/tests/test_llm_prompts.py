"""Tests for changelog_tool.llm.prompts (offline, no network).

Covers:
- build_classification_prompt: contains all §9.5 categories, strict JSON schema,
  all input SHAs in metadata, estimate_chars is monotonic
- build_missing_line_prompt: contains subject/body/files/category, §11.8 constraints
- estimate_chars: monotonic with growing input
"""

from __future__ import annotations

import json

from changelog_tool.llm.prompts import (
    build_classification_prompt,
    build_missing_line_prompt,
    estimate_chars,
)

SHA1 = "a" * 40
SHA2 = "b" * 40

_CONTEXT1 = {
    "sha": SHA1,
    "subject": "Add Redis pipelining",
    "body": "Implements pipeline support.",
    "changed_files": ["redis/pipeline.cpp"],
    "insertions": 200,
    "deletions": 10,
    "files_count": 1,
    "size_score": 210,
    "commit_url": f"https://github.com/org/repo/commit/{SHA1}",
}

_CONTEXT2 = {
    "sha": SHA2,
    "subject": "Fix memory leak in pool",
    "body": "",
    "changed_files": ["pool.cpp"],
    "insertions": 5,
    "deletions": 3,
    "files_count": 1,
    "size_score": 8,
    "commit_url": f"https://github.com/org/repo/commit/{SHA2}",
}


class TestBuildClassificationPrompt:
    def test_returns_llm_request(self):
        from changelog_tool.llm.base import LlmRequest
        req = build_classification_prompt([_CONTEXT1])
        assert isinstance(req, LlmRequest)

    def test_system_contains_all_spec_categories(self):
        req = build_classification_prompt([_CONTEXT1])
        for cat in ("feature", "improvement", "breaking_change", "important_bugfix",
                    "internal", "docs", "tests", "chore", "unclear"):
            assert cat in req.system, f"Category '{cat}' missing from system prompt"

    def test_system_contains_json_schema_keys(self):
        req = build_classification_prompt([_CONTEXT1])
        for key in ("items", "stats", "input_count", "output_count",
                    "candidate_for_changelog", "changelog_line", "confidence"):
            assert key in req.system, f"Schema key '{key}' missing from system prompt"

    def test_user_contains_sha(self):
        req = build_classification_prompt([_CONTEXT1])
        assert SHA1 in req.user

    def test_user_is_valid_json(self):
        req = build_classification_prompt([_CONTEXT1])
        parsed = json.loads(req.user)
        assert isinstance(parsed, list)
        assert parsed[0]["sha"] == SHA1

    def test_metadata_sha_list(self):
        req = build_classification_prompt([_CONTEXT1, _CONTEXT2])
        assert req.metadata["sha_list"] == [SHA1, SHA2]
        assert req.metadata["batch_size"] == 2

    def test_empty_batch(self):
        req = build_classification_prompt([])
        assert req.metadata["batch_size"] == 0
        assert req.metadata["sha_list"] == []

    def test_system_contains_do_not_skip_instruction(self):
        req = build_classification_prompt([_CONTEXT1])
        assert "Do not skip" in req.system or "do not skip" in req.system.lower()

    def test_system_contains_every_sha_once_instruction(self):
        req = build_classification_prompt([_CONTEXT1])
        assert "exactly once" in req.system


class TestBuildMissingLinePrompt:
    def test_returns_llm_request(self):
        from changelog_tool.llm.base import LlmRequest
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert isinstance(req, LlmRequest)

    def test_user_contains_subject(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert "Add Redis pipelining" in req.user

    def test_user_contains_category(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert "feature" in req.user

    def test_user_contains_changed_files(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert "redis/pipeline.cpp" in req.user

    def test_maintainer_comment_included_when_provided(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature", "Important for users.")
        assert "Important for users." in req.user

    def test_maintainer_comment_absent_when_none(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature", None)
        assert "Maintainer" not in req.user

    def test_system_says_one_line(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert "one" in req.system.lower() or "One" in req.system

    def test_system_says_no_markdown_bullets(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert "bullet" in req.system.lower() or "Markdown" in req.system

    def test_metadata_contains_sha(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert req.metadata["sha"] == SHA1

    def test_metadata_contains_category(self):
        req = build_missing_line_prompt(_CONTEXT1, "feature")
        assert req.metadata["category"] == "feature"


class TestEstimateChars:
    def test_returns_int(self):
        req = build_classification_prompt([_CONTEXT1])
        assert isinstance(estimate_chars(req), int)

    def test_positive(self):
        req = build_classification_prompt([_CONTEXT1])
        assert estimate_chars(req) > 0

    def test_monotonic_with_more_commits(self):
        req1 = build_classification_prompt([_CONTEXT1])
        req2 = build_classification_prompt([_CONTEXT1, _CONTEXT2])
        assert estimate_chars(req2) > estimate_chars(req1)

    def test_equals_system_plus_user_length(self):
        req = build_classification_prompt([_CONTEXT1])
        assert estimate_chars(req) == len(req.system) + len(req.user)
