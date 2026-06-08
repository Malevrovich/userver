"""Tests for LLM backends (offline — HTTP stubbed, no real network).

Covers:
- HttpApiBackend: transient vs permanent HTTP errors, successful response
  parsing, timeout mapping (api_key/base_url/model are now required args)
- FakeBackend: canned classification response, canned missing-line response,
  default unclear for unknown SHA, call_count tracking, response_fn override
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from changelog_tool.llm.backends.fake import FakeBackend
from changelog_tool.llm.backends.http_api import HttpApiBackend, ENV_API_KEY
from changelog_tool.llm.base import LlmRequest
from changelog_tool.llm.errors import LlmError, LlmTransientError

SHA1 = "a" * 40
SHA2 = "b" * 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(sha_list=None, single_sha="") -> LlmRequest:
    sha_list = sha_list or []
    return LlmRequest(
        system="You are a helpful assistant.",
        user="Classify these commits.",
        metadata={"sha_list": sha_list, "sha": single_sha},
    )


def _openai_response(content: str) -> bytes:
    """Build a minimal OpenAI-compatible chat completion JSON response."""
    payload = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    return json.dumps(payload).encode("utf-8")


class _FakeHTTPResponse:
    """Minimal file-like object that urllib.urlopen returns."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# HttpApiBackend
# ---------------------------------------------------------------------------


def _make_backend(**kwargs) -> HttpApiBackend:
    """Construct an HttpApiBackend with required args, allowing overrides."""
    defaults = dict(model="gpt-4o-mini", api_key="test-key", base_url="https://api.example.com/v1")
    defaults.update(kwargs)
    return HttpApiBackend(**defaults)


class TestHttpApiBackend:
    def test_successful_response(self, monkeypatch):
        content = json.dumps({
            "items": [
                {"sha": SHA1, "category": "feature", "candidate_for_changelog": True,
                 "changelog_line": "Added X.", "comment": "Good.", "confidence": "high"}
            ],
            "stats": {"input_count": 1, "output_count": 1},
        })
        fake_resp = _FakeHTTPResponse(_openai_response(content))

        with patch("urllib.request.urlopen", return_value=fake_resp):
            backend = _make_backend()
            response = backend.complete(_make_request([SHA1]))

        assert response.backend == "http_api"
        assert SHA1 in response.text
        assert response.usage is not None

    def test_http_429_raises_transient(self, monkeypatch):
        exc = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests",
            hdrs=None, fp=BytesIO(b"rate limited"),
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(LlmTransientError, match="429"):
                _make_backend().complete(_make_request([SHA1]))

    def test_http_500_raises_transient(self):
        exc = urllib.error.HTTPError(
            url="http://x", code=500, msg="Internal Server Error",
            hdrs=None, fp=BytesIO(b"server error"),
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(LlmTransientError, match="500"):
                _make_backend().complete(_make_request([SHA1]))

    def test_http_401_raises_permanent(self):
        exc = urllib.error.HTTPError(
            url="http://x", code=401, msg="Unauthorized",
            hdrs=None, fp=BytesIO(b"unauthorized"),
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(LlmError) as exc_info:
                _make_backend().complete(_make_request([SHA1]))
            # Must be permanent LlmError, NOT LlmTransientError
            assert type(exc_info.value) is LlmError

    def test_http_403_raises_permanent(self):
        exc = urllib.error.HTTPError(
            url="http://x", code=403, msg="Forbidden",
            hdrs=None, fp=BytesIO(b"forbidden"),
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(LlmError) as exc_info:
                _make_backend().complete(_make_request([SHA1]))
            assert type(exc_info.value) is LlmError

    def test_url_error_raises_transient(self):
        exc = urllib.error.URLError(reason="Connection refused")
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(LlmTransientError, match="Network error"):
                _make_backend().complete(_make_request([SHA1]))

    def test_timeout_raises_transient(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with pytest.raises(LlmTransientError, match="Timeout"):
                _make_backend().complete(_make_request([SHA1]))

    def test_non_json_response_raises_transient(self):
        fake_resp = _FakeHTTPResponse(b"not json at all")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(LlmTransientError, match="non-JSON"):
                _make_backend().complete(_make_request([SHA1]))

    def test_missing_choices_raises_transient(self):
        fake_resp = _FakeHTTPResponse(json.dumps({"no_choices": True}).encode())
        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(LlmTransientError, match="response shape"):
                _make_backend().complete(_make_request([SHA1]))

    def test_model_from_constructor(self):
        backend = _make_backend(model="gpt-4o")
        assert backend._model == "gpt-4o"

    def test_base_url_trailing_slash_stripped(self):
        backend = _make_backend(base_url="https://api.example.com/v1/")
        assert not backend._base_url.endswith("/")


# ---------------------------------------------------------------------------
# FakeBackend
# ---------------------------------------------------------------------------


class TestFakeBackend:
    def test_returns_llm_response(self):
        backend = FakeBackend()
        req = _make_request([SHA1])
        resp = backend.complete(req)
        assert resp.backend == "fake"
        assert isinstance(resp.text, str)

    def test_canned_classification_response(self):
        canned = {
            SHA1: {
                "category": "feature",
                "candidate_for_changelog": True,
                "changelog_line": "Added X.",
                "comment": "Good.",
                "confidence": "high",
            }
        }
        backend = FakeBackend(responses=canned)
        req = _make_request([SHA1])
        resp = backend.complete(req)
        data = json.loads(resp.text)
        assert data["items"][0]["category"] == "feature"
        assert data["items"][0]["sha"] == SHA1

    def test_default_unclear_for_unknown_sha(self):
        backend = FakeBackend()
        req = _make_request([SHA1])
        resp = backend.complete(req)
        data = json.loads(resp.text)
        assert data["items"][0]["category"] == "unclear"

    def test_stats_match_sha_list(self):
        backend = FakeBackend()
        req = _make_request([SHA1, SHA2])
        resp = backend.complete(req)
        data = json.loads(resp.text)
        assert data["stats"]["input_count"] == 2
        assert data["stats"]["output_count"] == 2

    def test_canned_missing_line_response(self):
        canned = {SHA1: {"changelog_line": "Added Redis pipelining."}}
        backend = FakeBackend(responses=canned)
        req = LlmRequest(
            system="sys", user="user",
            metadata={"sha": SHA1, "sha_list": []},
        )
        resp = backend.complete(req)
        assert resp.text == "Added Redis pipelining."

    def test_default_missing_line_when_no_canned(self):
        backend = FakeBackend(default_line="Default line.")
        req = LlmRequest(
            system="sys", user="user",
            metadata={"sha": SHA1, "sha_list": []},
        )
        resp = backend.complete(req)
        assert resp.text == "Default line."

    def test_call_count_increments(self):
        backend = FakeBackend()
        assert backend.call_count == 0
        backend.complete(_make_request([SHA1]))
        assert backend.call_count == 1
        backend.complete(_make_request([SHA2]))
        assert backend.call_count == 2

    def test_last_request_stored(self):
        backend = FakeBackend()
        req = _make_request([SHA1])
        backend.complete(req)
        assert backend.last_request is req

    def test_response_fn_takes_precedence(self):
        def fn(request):
            return "custom response"

        backend = FakeBackend(response_fn=fn)
        resp = backend.complete(_make_request([SHA1]))
        assert resp.text == "custom response"

    def test_empty_sha_list_returns_empty_items(self):
        backend = FakeBackend()
        req = _make_request([])
        resp = backend.complete(req)
        data = json.loads(resp.text)
        assert data["items"] == []
        assert data["stats"]["input_count"] == 0
