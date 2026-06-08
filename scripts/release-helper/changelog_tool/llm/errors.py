"""LLM error hierarchy for the CHANGELOG preparation tool.

All LLM-related exceptions inherit from :class:`LlmError`.

Callers (T7 stage-4 classification, T10 missing-line generation) use the
subclass type to decide whether to retry or fall back to ``unclear``:

- :class:`LlmTransientError` — retryable (network timeout, rate limit, 5xx,
  non-zero subprocess exit).  T7/T10 retry once then write fallback ``unclear``
  (Spec §9.10, §14.3).  For HTTP 429 responses, the optional
  :attr:`~LlmTransientError.retry_after` attribute carries the number of
  seconds the caller should wait before retrying (from the ``Retry-After``
  response header).  The optional :attr:`~LlmTransientError.is_rate_limit`
  flag distinguishes 429 from other transient errors so the caller can apply
  RPS-aware backoff.
- :class:`LlmResponseError` — the model returned something structurally wrong
  (invalid JSON after cleanup, invariant violation).  Raised by
  :mod:`~changelog_tool.llm.parsing`, not by backends.  Also retryable once
  per §9.10.
- :class:`LlmError` (base) — permanent / configuration error (bad API key,
  unknown backend, missing binary).  Not retried.
"""

from __future__ import annotations

from typing import Optional


class LlmError(Exception):
    """Base class for all LLM-related errors.

    Permanent errors (auth failure, unknown backend, missing binary) raise
    this directly.  Callers should **not** retry on bare :class:`LlmError`.
    """


class LlmTransientError(LlmError):
    """Retryable error from the LLM backend.

    Raised when the failure is likely temporary:

    - Network timeout or connection refused.
    - HTTP 429 (rate limit) or 5xx server error.
    - Non-zero subprocess exit from an agent CLI.

    For HTTP 429 responses, the backend sets:

    - :attr:`is_rate_limit` = ``True``
    - :attr:`retry_after` = seconds to wait (from ``Retry-After`` header), or
      ``None`` if the header was absent.

    T7 and T10 catch this and:

    - For 429: wait ``max(retry_after or 0, 1/max_rps)`` seconds, then retry
      up to ``max_429_retries`` times before falling back to ``unclear``.
    - For other transient errors: retry once, then fall back to ``unclear``
      (Spec §9.10, §14.3).
    """

    def __init__(
        self,
        message: str,
        *,
        is_rate_limit: bool = False,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.is_rate_limit: bool = is_rate_limit
        self.retry_after: Optional[float] = retry_after


class LlmResponseError(LlmError):
    """The model returned a response that could not be parsed or violated invariants.

    Raised by :mod:`~changelog_tool.llm.parsing` after JSON extraction and
    cleanup have already been attempted (Spec §9.10, §9.12).

    T7 and T10 treat this the same as :class:`LlmTransientError`: retry once,
    then fall back to ``unclear``.
    """


__all__ = [
    "LlmError",
    "LlmTransientError",
    "LlmResponseError",
]
