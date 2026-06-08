"""Factory for building LLM clients from configuration.

The single entry point is :func:`build_client`, which reads ``llm.backend``
from the resolved :class:`~changelog_tool.config.LlmConfig` and returns the
appropriate :class:`~changelog_tool.llm.base.LlmClient` implementation.

For the ``http_api`` backend, connection credentials are resolved in this
priority order:

1. Explicit keyword arguments (passed from CLI flags ``--llm-api-key`` /
   ``--llm-base-url`` / ``--llm-model``).
2. Environment variables ``CHANGELOG_LLM_API_KEY`` / ``CHANGELOG_LLM_BASE_URL``
   / ``CHANGELOG_LLM_MODEL``.
3. Interactive prompt (``getpass`` for the API key, plain ``input`` for the
   base URL) — only when running in an interactive terminal.

If a required value cannot be obtained through any of the above, a
:class:`~changelog_tool.llm.errors.LlmError` is raised with a clear message.

Supported backends
------------------
- ``http_api`` (default) — :class:`~changelog_tool.llm.backends.http_api.HttpApiBackend`
- ``fake``               — :class:`~changelog_tool.llm.backends.fake.FakeBackend`

The ``agent`` backend (opencode / codex) is planned but not yet implemented;
see ``plans/T6-agent-backend-plan.md``.
"""

from __future__ import annotations

import getpass
import os
import sys
from typing import Optional

from changelog_tool.config import LLM_BACKEND_FAKE, LLM_BACKEND_HTTP_API, LlmConfig
from changelog_tool.llm.backends.http_api import ENV_API_KEY, ENV_BASE_URL, ENV_MODEL
from changelog_tool.llm.base import LlmClient
from changelog_tool.llm.errors import LlmError


def build_client(
    cfg: LlmConfig,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> LlmClient:
    """Build and return an :class:`~changelog_tool.llm.base.LlmClient` for *cfg*.

    Args:
        cfg:      The resolved ``llm`` section of ``changelog.yaml``.
        api_key:  Explicit API key (from ``--llm-api-key`` CLI flag).
                  Falls back to ``CHANGELOG_LLM_API_KEY`` env var, then
                  interactive prompt.
        base_url: Explicit base URL (from ``--llm-base-url`` CLI flag).
                  Falls back to ``CHANGELOG_LLM_BASE_URL`` env var, then
                  interactive prompt.
        model:    Explicit model string (from ``--llm-model`` CLI flag).
                  Falls back to ``CHANGELOG_LLM_MODEL`` env var, then
                  ``cfg.model``.

    Returns:
        A concrete :class:`~changelog_tool.llm.base.LlmClient` instance.

    Raises:
        LlmError: The backend name is unknown, or a required credential could
                  not be obtained (not in CLI flags, env vars, or interactive
                  input).
    """
    backend = cfg.backend

    if backend == LLM_BACKEND_HTTP_API:
        from changelog_tool.llm.backends.http_api import HttpApiBackend

        resolved_key = _resolve_required(
            value=api_key,
            env_var=ENV_API_KEY,
            prompt_label="LLM API key",
            secret=True,
        )
        resolved_url = _resolve_required(
            value=base_url,
            env_var=ENV_BASE_URL,
            prompt_label="LLM API base URL (e.g. https://api.openai.com/v1)",
            secret=False,
        )
        resolved_model = (
            model
            or os.environ.get(ENV_MODEL)
            or cfg.model
            or None
        )
        if not resolved_model:
            raise LlmError(
                "LLM model not specified. "
                "Set it via --llm-model, the CHANGELOG_LLM_MODEL env var, "
                "or llm.model in changelog.yaml."
            )

        return HttpApiBackend(
            model=resolved_model,
            api_key=resolved_key,
            base_url=resolved_url,
        )

    if backend == LLM_BACKEND_FAKE:
        from changelog_tool.llm.backends.fake import FakeBackend

        return FakeBackend()

    raise LlmError(
        f"Unknown LLM backend '{backend}'. "
        f"Supported values: '{LLM_BACKEND_HTTP_API}', '{LLM_BACKEND_FAKE}'."
    )


def _resolve_required(
    value: Optional[str],
    env_var: str,
    prompt_label: str,
    secret: bool,
) -> str:
    """Resolve a required credential through CLI → env → interactive prompt.

    Args:
        value:        Explicit value from CLI flag (may be ``None``).
        env_var:      Name of the fallback environment variable.
        prompt_label: Human-readable label shown in the interactive prompt.
        secret:       If ``True``, use ``getpass`` (no echo); otherwise plain
                      ``input``.

    Returns:
        The resolved non-empty string value.

    Raises:
        LlmError: The value could not be obtained (non-interactive terminal and
                  no CLI flag / env var).
    """
    # 1. Explicit CLI flag.
    if value:
        return value

    # 2. Environment variable.
    env_value = os.environ.get(env_var, "").strip()
    if env_value:
        return env_value

    # 3. Interactive prompt — only when stdin is a real terminal.
    if sys.stdin.isatty():
        try:
            if secret:
                prompted = getpass.getpass(f"{prompt_label}: ").strip()
            else:
                prompted = input(f"{prompt_label}: ").strip()
            if prompted:
                return prompted
        except (EOFError, KeyboardInterrupt):
            pass

    raise LlmError(
        f"{prompt_label} not provided. "
        f"Pass it via the corresponding CLI flag or set the {env_var} "
        f"environment variable."
    )


__all__ = ["build_client"]
