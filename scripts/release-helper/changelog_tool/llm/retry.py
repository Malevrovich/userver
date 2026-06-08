"""Rate limiting and retry logic for LLM clients.

Provides a token-bucket rate limiter and a generic retry wrapper for LLM calls
to handle transient errors and rate limits (429s).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Coroutine, TypeVar

from changelog_tool.llm.errors import LlmError, LlmResponseError, LlmTransientError

T = TypeVar("T")


class TokenBucket:
    """Simple async token-bucket rate limiter.

    Args:
        rate: Target requests per second (0 = unlimited).
    """

    def __init__(self, rate: float) -> None:
        self._rate = rate  # tokens per second
        # Start with 1 token so the first request is free; subsequent requests
        # must wait for the bucket to refill at `rate` tokens/second.
        self._tokens: float = 1.0 if rate > 0 else 0.0
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it.

        The lock is held for the entire duration including the sleep so that
        concurrent callers are serialized and each waits its fair share.
        """
        if self._rate <= 0:
            return  # unlimited

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Need to wait for the next token.  Sleep while holding the lock
            # so that the next caller waits until this one has finished.
            wait_time = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0
            await asyncio.sleep(wait_time)
            # Update last_refill after sleeping so the next caller gets a
            # fresh refill calculation from the correct baseline.
            self._last_refill = time.monotonic()


async def execute_with_retry(
    operation: Callable[[], Coroutine[Any, Any, T]],
    batch_id: str,
    verbose: bool,
    max_rps: float = 0.0,
    max_429_retries: int = 5,
) -> tuple[T | None, str | None]:
    """Execute an async LLM operation with retries.

    **429 rate-limit retries** (up to *max_429_retries*):
    When the backend raises :class:`~changelog_tool.llm.errors.LlmTransientError`
    with ``is_rate_limit=True``, wait ``max(retry_after, 1/max_rps)`` seconds
    (exponential backoff if no ``Retry-After`` header) and retry.  These retries
    do **not** count against the one-shot non-429 retry budget.

    **Non-429 transient / response-error retry** (once):
    Any other :class:`~changelog_tool.llm.errors.LlmTransientError` or
    :class:`~changelog_tool.llm.errors.LlmResponseError` is retried exactly
    once.  On second failure, return ``(None, error_message)``.

    **Permanent errors** (:class:`~changelog_tool.llm.errors.LlmError`):
    Not retried; return ``(None, error_message)`` immediately.

    Args:
        operation:       Async callable that performs the LLM request and parsing.
        batch_id:        Identifier for logging (e.g. "batch-0001" or SHA).
        verbose:         Whether to print detailed error messages.
        max_rps:         Target requests per second (used for minimum 429 wait).
        max_429_retries: Max times to retry a 429 before failing.

    Returns:
        A tuple ``(result, error_message)``. If successful, ``error_message``
        is None. If failed, ``result`` is None.
    """
    min_rps_wait = (1.0 / max_rps) if max_rps > 0 else 0.0
    non_429_attempts = 0
    error_msg: str | None = None

    while True:
        try:
            result = await operation()
            return result, None

        except LlmTransientError as exc:
            error_msg = str(exc)

            if exc.is_rate_limit:
                if max_429_retries <= 0:
                    if verbose:
                        print(
                            f"    {batch_id}: 429 rate limit, max_429_retries=0, "
                            f"falling back."
                        )
                    return None, error_msg

                retry_after = exc.retry_after or 0.0
                wait = max(retry_after, min_rps_wait)
                if wait <= 0:
                    wait = 1.0

                max_429_retries -= 1
                if verbose:
                    print(
                        f"    {batch_id}: 429 rate limit, waiting {wait:.1f}s "
                        f"({max_429_retries} retries left)..."
                    )
                await asyncio.sleep(wait)
                continue

            else:
                non_429_attempts += 1
                if verbose or non_429_attempts >= 2:
                    print(
                        f"    {batch_id} attempt {non_429_attempts} failed: "
                        f"{error_msg[:200]}"
                    )
                if non_429_attempts < 2:
                    if verbose:
                        print(f"    {batch_id}: retrying...")
                    continue
                return None, error_msg

        except LlmResponseError as exc:
            error_msg = str(exc)
            non_429_attempts += 1
            if verbose or non_429_attempts >= 2:
                print(
                    f"    {batch_id} attempt {non_429_attempts} failed "
                    f"(invalid response): {error_msg[:200]}"
                )
            if non_429_attempts < 2:
                if verbose:
                    print(f"    {batch_id}: retrying...")
                continue
            return None, error_msg

        except LlmError as exc:
            return None, str(exc)


__all__ = ["TokenBucket", "execute_with_retry"]
