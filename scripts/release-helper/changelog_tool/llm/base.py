"""Core interface types for the LLM abstraction layer.

Defines the provider-neutral :class:`LlmRequest` / :class:`LlmResponse`
dataclasses and the :class:`LlmClient` base class that all backends implement.

Design principles
-----------------
- One call = one prompt = one response.  Batching, retries, and state are the
  caller's responsibility (T7 / T10) so resumability logic lives in one place
  and is identical across backends.
- The interface exposes both a synchronous :meth:`complete` and an async
  :meth:`async_complete`.  The default async implementation runs the sync
  method in the default executor (thread pool), so backends only need to
  override one of the two.  High-throughput backends (e.g. aiohttp) can
  override :meth:`async_complete` directly.
- Backends may raise :class:`~changelog_tool.llm.errors.LlmTransientError` for
  retryable failures or :class:`~changelog_tool.llm.errors.LlmError` for
  permanent ones.  JSON validation is **not** the backend's job — that belongs
  to :mod:`~changelog_tool.llm.parsing`.
- No provider-specific types leak through this interface.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, Dict, Optional


@dataclasses.dataclass
class LlmRequest:
    """A single prompt sent to the LLM.

    Args:
        system:          System instruction (role: system).
        user:            User message containing the actual task / data.
        max_output_chars: Soft hint to the backend; may be ignored if the
                          provider does not support it.
        temperature:     Sampling temperature (0.0–2.0); ``None`` uses the
                         backend default.
        metadata:        Free-form dict for logging / tracing (e.g. batch id,
                         SHA list).  Not sent to the provider.
    """

    system: str
    user: str
    max_output_chars: Optional[int] = None
    temperature: Optional[float] = None
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class LlmResponse:
    """Raw response from the LLM backend.

    Args:
        text:    The model's output text (may be JSON, plain text, or
                 Markdown-wrapped JSON — callers use
                 :mod:`~changelog_tool.llm.parsing` to extract structure).
        backend: Name of the backend that produced this response
                 (e.g. ``"http_api"``, ``"fake"``).
        raw:     Optional provider-specific payload for debugging / logging.
        usage:   Optional token-usage dict (``{"prompt_tokens": …,
                 "completion_tokens": …}``).
    """

    text: str
    backend: str
    raw: Optional[Any] = None
    usage: Optional[Dict[str, int]] = None


class LlmClient:
    """Base class for LLM backends.

    Backends must implement at least one of :meth:`complete` (sync) or
    :meth:`async_complete` (async).

    The default :meth:`async_complete` runs :meth:`complete` in the default
    asyncio executor (thread pool), so sync-only backends get async support
    for free.  Backends that natively support async I/O should override
    :meth:`async_complete` directly.

    Backends must raise:

    - :class:`~changelog_tool.llm.errors.LlmTransientError` for retryable
      failures (timeout, rate limit, 5xx, non-zero subprocess exit).
    - :class:`~changelog_tool.llm.errors.LlmError` for permanent failures
      (bad API key, missing binary, unknown backend).

    Backends must **not** validate JSON or enforce spec invariants — that is
    :mod:`~changelog_tool.llm.parsing`'s job.
    """

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Send *request* to the LLM and return the raw response (synchronous).

        Args:
            request: The prompt to send.

        Returns:
            :class:`LlmResponse` with the model's raw text output.

        Raises:
            LlmTransientError: Retryable failure.
            LlmError:          Permanent failure.
        """
        raise NotImplementedError  # pragma: no cover

    async def async_complete(self, request: LlmRequest) -> LlmResponse:
        """Send *request* to the LLM and return the raw response (async).

        The default implementation runs :meth:`complete` in the default
        executor (thread pool).  Override this method for native async I/O.

        Args:
            request: The prompt to send.

        Returns:
            :class:`LlmResponse` with the model's raw text output.

        Raises:
            LlmTransientError: Retryable failure.
            LlmError:          Permanent failure.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.complete, request)


__all__ = [
    "LlmRequest",
    "LlmResponse",
    "LlmClient",
]
