"""Tests for changelog_tool.llm.factory (offline, no network).

Covers:
- build_client returns the right backend per config
- http_api backend: credentials resolved from explicit kwargs, env vars
- http_api backend: missing api_key / base_url / model raises LlmError
- Unknown backend raises LlmError with a clear message
"""

from __future__ import annotations

import pytest

from changelog_tool.config import LlmConfig, LLM_BACKEND_HTTP_API, LLM_BACKEND_FAKE
from changelog_tool.llm.backends.fake import FakeBackend
from changelog_tool.llm.backends.http_api import HttpApiBackend, ENV_API_KEY, ENV_BASE_URL, ENV_MODEL
from changelog_tool.llm.errors import LlmError
from changelog_tool.llm.factory import build_client

_KEY = "sk-test-key"
_URL = "https://api.example.com/v1"
_MODEL = "gpt-4o-mini"


class TestBuildClientFake:
    def test_fake_backend_returned_for_fake(self):
        cfg = LlmConfig(backend=LLM_BACKEND_FAKE)
        client = build_client(cfg)
        assert isinstance(client, FakeBackend)

    def test_unknown_backend_raises_llm_error(self):
        cfg = LlmConfig(backend="totally_unknown_backend")
        with pytest.raises(LlmError, match="totally_unknown_backend"):
            build_client(cfg)

    def test_unknown_backend_error_mentions_supported_values(self):
        cfg = LlmConfig(backend="agent_not_yet")
        with pytest.raises(LlmError) as exc_info:
            build_client(cfg)
        msg = str(exc_info.value)
        assert LLM_BACKEND_HTTP_API in msg or LLM_BACKEND_FAKE in msg


class TestBuildClientHttpApi:
    def test_http_api_backend_returned_with_explicit_args(self):
        cfg = LlmConfig(backend=LLM_BACKEND_HTTP_API)
        client = build_client(cfg, api_key=_KEY, base_url=_URL, model=_MODEL)
        assert isinstance(client, HttpApiBackend)

    def test_http_api_backend_returned_with_env_vars(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, _KEY)
        monkeypatch.setenv(ENV_BASE_URL, _URL)
        monkeypatch.setenv(ENV_MODEL, _MODEL)
        cfg = LlmConfig(backend=LLM_BACKEND_HTTP_API)
        client = build_client(cfg)
        assert isinstance(client, HttpApiBackend)

    def test_explicit_args_take_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, "env-key")
        monkeypatch.setenv(ENV_BASE_URL, "https://env.example.com/v1")
        monkeypatch.setenv(ENV_MODEL, "env-model")
        cfg = LlmConfig(backend=LLM_BACKEND_HTTP_API)
        client = build_client(cfg, api_key=_KEY, base_url=_URL, model=_MODEL)
        assert isinstance(client, HttpApiBackend)
        assert client._api_key == _KEY
        assert client._base_url == _URL.rstrip("/")
        assert client._model == _MODEL

    def test_model_from_config_llm_model(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, _KEY)
        monkeypatch.setenv(ENV_BASE_URL, _URL)
        monkeypatch.delenv(ENV_MODEL, raising=False)
        cfg = LlmConfig(backend=LLM_BACKEND_HTTP_API, model="config-model")
        client = build_client(cfg)
        assert isinstance(client, HttpApiBackend)
        assert client._model == "config-model"

    def test_missing_api_key_raises_llm_error(self, monkeypatch):
        monkeypatch.delenv(ENV_API_KEY, raising=False)
        monkeypatch.setenv(ENV_BASE_URL, _URL)
        monkeypatch.setenv(ENV_MODEL, _MODEL)
        cfg = LlmConfig(backend=LLM_BACKEND_HTTP_API)
        # stdin is not a tty in tests, so interactive prompt is skipped
        with pytest.raises(LlmError, match=ENV_API_KEY):
            build_client(cfg)

    def test_missing_base_url_raises_llm_error(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, _KEY)
        monkeypatch.delenv(ENV_BASE_URL, raising=False)
        monkeypatch.setenv(ENV_MODEL, _MODEL)
        cfg = LlmConfig(backend=LLM_BACKEND_HTTP_API)
        with pytest.raises(LlmError, match=ENV_BASE_URL):
            build_client(cfg)

    def test_missing_model_raises_llm_error(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, _KEY)
        monkeypatch.setenv(ENV_BASE_URL, _URL)
        monkeypatch.delenv(ENV_MODEL, raising=False)
        cfg = LlmConfig(backend=LLM_BACKEND_HTTP_API, model=None)
        with pytest.raises(LlmError, match="model"):
            build_client(cfg)

    def test_default_backend_is_http_api(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, _KEY)
        monkeypatch.setenv(ENV_BASE_URL, _URL)
        monkeypatch.setenv(ENV_MODEL, _MODEL)
        cfg = LlmConfig()  # uses DEFAULT_LLM_BACKEND
        client = build_client(cfg)
        assert isinstance(client, HttpApiBackend)
