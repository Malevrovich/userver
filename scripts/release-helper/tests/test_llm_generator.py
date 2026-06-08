"""Tests for changelog_tool.llm_generator."""

from __future__ import annotations

import json
import os

import pytest

from changelog_tool.llm.base import LlmClient, LlmRequest, LlmResponse
from changelog_tool.llm.errors import LlmError, LlmResponseError, LlmTransientError
from changelog_tool.llm_generator import (
    TODO_FALLBACK_PREFIX,
    _clean_response,
    run_line_generation,
)
from changelog_tool.models import (
    ClassificationSource,
    Confidence,
    LlmCategory,
    SizeBucket,
    VerifiedCommit,
)


class FakeClient(LlmClient):
    def __init__(self, responses: dict[str, str | Exception]):
        self.responses = responses
        self.calls = 0

    def complete(self, request: LlmRequest) -> LlmResponse:
        raise NotImplementedError()

    async def async_complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        sha = request.metadata.get("sha", "")
        resp = self.responses.get(sha, "Default line")
        if isinstance(resp, Exception):
            raise resp
        return LlmResponse(text=resp, usage={"prompt_tokens": 10, "completion_tokens": 10}, backend="fake")


def _make_verified_commit(sha: str, candidate: bool, line: str | None = None) -> VerifiedCommit:
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
        candidate_for_changelog=candidate,
        changelog_line=line,
        classification_comment=None,
        confidence=Confidence.HIGH,
        is_merge_commit=False,
        attention_flags=[],
    )


def test_clean_response():
    assert _clean_response("Just a line") == "Just a line"
    assert _clean_response("- Bullet point") == "Bullet point"
    assert _clean_response("* Bullet point") == "Bullet point"
    assert _clean_response('"Quoted line"') == "Quoted line"
    assert _clean_response("```\nCode block\n```") == "Code block"
    assert _clean_response("```markdown\nCode block\n```") == "Code block"
    assert _clean_response("Line 1\nLine 2") == "Line 1"


def test_run_line_generation_success(tmp_path):
    c1 = _make_verified_commit("1" * 40, candidate=True, line=None)
    c2 = _make_verified_commit("2" * 40, candidate=True, line="Already has line")
    c3 = _make_verified_commit("3" * 40, candidate=False, line=None)

    client = FakeClient({"1" * 40: "Generated line"})
    state_path = tmp_path / "state.json"

    result = run_line_generation([c1, c2, c3], client, str(state_path))

    assert result.total_needed == 1
    assert result.items_done == 1
    assert result.items_failed == 0
    assert result.items_skipped_already_done == 0

    assert c1.changelog_line == "Generated line"
    assert c2.changelog_line == "Already has line"
    assert c3.changelog_line is None

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["items"]["1" * 40]["status"] == "done"
    assert state["items"]["1" * 40]["changelog_line"] == "Generated line"


def test_run_line_generation_fallback(tmp_path):
    c1 = _make_verified_commit("1" * 40, candidate=True, line=None)

    client = FakeClient({"1" * 40: LlmError("Permanent error")})
    state_path = tmp_path / "state.json"

    result = run_line_generation([c1], client, str(state_path))

    assert result.total_needed == 1
    assert result.items_done == 0
    assert result.items_failed == 1

    assert c1.changelog_line == f"{TODO_FALLBACK_PREFIX} Subject for 11111111"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["items"]["1" * 40]["status"] == "failed"
    assert state["items"]["1" * 40]["changelog_line"] == c1.changelog_line


def test_run_line_generation_resume(tmp_path):
    c1 = _make_verified_commit("1" * 40, candidate=True, line=None)
    c2 = _make_verified_commit("2" * 40, candidate=True, line=None)

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "operation": "changelog_line_generation",
        "items": {
            "1" * 40: {
                "status": "done",
                "changelog_line": "Resumed line",
            }
        }
    }), encoding="utf-8")

    client = FakeClient({"2" * 40: "New line"})

    result = run_line_generation([c1, c2], client, str(state_path))

    assert result.total_needed == 2
    assert result.items_done == 1
    assert result.items_skipped_already_done == 1

    assert c1.changelog_line == "Resumed line"
    assert c2.changelog_line == "New line"
    assert client.calls == 1
