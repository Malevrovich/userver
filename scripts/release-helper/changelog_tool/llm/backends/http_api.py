"""OpenAI-compatible HTTP API backend for the CHANGELOG preparation tool.

Sends a single stateless chat-completion request per :meth:`complete` call.
Uses only the Python standard library (``urllib``) — no extra dependencies.

All connection credentials (``api_key``, ``base_url``, ``model``) are
**required constructor arguments**.  The :func:`~changelog_tool.llm.factory.build_client`
factory resolves them from CLI flags → environment variables → interactive
prompt before constructing this class.  No defaults are applied here.

Environment variables (resolved by the factory, not this class):

- ``CHANGELOG_LLM_API_KEY``  — API key.
- ``CHANGELOG_LLM_BASE_URL`` — base URL of the API endpoint.
- ``CHANGELOG_LLM_MODEL``    — model identifier override.

Error mapping
-------------
- Timeout / connection error → :class:`~changelog_tool.llm.errors.LlmTransientError`
- HTTP 429 / 5xx            → :class:`~changelog_tool.llm.errors.LlmTransientError`
- HTTP 401 / 403 / 400      → :class:`~changelog_tool.llm.errors.LlmError` (permanent)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from changelog_tool.llm.base import LlmClient, LlmRequest, LlmResponse
from changelog_tool.llm.errors import LlmError, LlmTransientError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 120  # seconds

ENV_API_KEY = "CHANGELOG_LLM_API_KEY"
ENV_BASE_URL = "CHANGELOG_LLM_BASE_URL"
ENV_MODEL = "CHANGELOG_LLM_MODEL"

# HTTP status codes that indicate a transient (retryable) failure.
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# HTTP status codes that indicate a permanent (non-retryable) failure.
_PERMANENT_STATUS_CODES = frozenset({400, 401, 403, 404})


class HttpApiBackend(LlmClient):
    """Stateless OpenAI-compatible chat-completion backend.

    All three connection arguments are required.  Use
    :func:`~changelog_tool.llm.factory.build_client` to construct this class
    — it handles credential resolution (CLI flags → env vars → interactive
    prompt).

    Args:
        model:    Model identifier (e.g. ``"gpt-4o-mini"``).
        api_key:  Provider API key.
        base_url: Base URL of the API endpoint (e.g.
                  ``"https://api.openai.com/v1"``).
        timeout:  Request timeout in seconds.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # LlmClient interface
    # ------------------------------------------------------------------

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Send *request* to the chat-completion endpoint and return the response.

        Args:
            request: The prompt to send.

        Returns:
            :class:`~changelog_tool.llm.base.LlmResponse` with the model's
            raw text output.

        Raises:
            LlmError:          Permanent HTTP error (4xx).
            LlmTransientError: Network error, timeout, or transient HTTP error
                               (429 / 5xx).
        """
        payload = self._build_payload(request)
        raw_response = self._post(payload)
        text = self._extract_text(raw_response)
        usage = raw_response.get("usage")

        return LlmResponse(
            text=text,
            backend="http_api",
            raw=raw_response,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(self, request: LlmRequest) -> Dict[str, Any]:
        """Build the JSON payload for the chat-completion API."""
        messages = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        # Request JSON output when the model supports it.
        # We always ask for JSON because our prompts demand it.
        payload["response_format"] = {"type": "json_object"}
        return payload

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST *payload* to the completions endpoint and return parsed JSON.

        Raises:
            LlmTransientError: Network / timeout / 429 / 5xx.
            LlmError:          Permanent HTTP error (4xx other than 429).
        """
        url = f"{self._base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"OAuth {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                error_body = "(could not read error body)"

            if status == 429:
                # Parse Retry-After header (seconds or HTTP-date).
                retry_after = _parse_retry_after(exc)
                raise LlmTransientError(
                    f"HTTP 429 rate limit from LLM API: {error_body[:200]}",
                    is_rate_limit=True,
                    retry_after=retry_after,
                ) from exc

            if status in _TRANSIENT_STATUS_CODES:
                raise LlmTransientError(
                    f"HTTP {status} from LLM API: {error_body[:500]}"
                ) from exc
            if status in _PERMANENT_STATUS_CODES:
                raise LlmError(
                    f"HTTP {status} from LLM API (permanent): {error_body[:500]}"
                ) from exc
            # Unknown status — treat as transient to be safe.
            raise LlmTransientError(
                f"HTTP {status} from LLM API: {error_body[:500]}"
            ) from exc

        except urllib.error.URLError as exc:
            raise LlmTransientError(
                f"Network error calling LLM API: {exc.reason}"
            ) from exc

        except TimeoutError as exc:
            raise LlmTransientError(
                f"Timeout calling LLM API after {self._timeout}s"
            ) from exc

        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LlmTransientError(
                f"LLM API returned non-JSON response: {exc}"
            ) from exc

    @staticmethod
    def _extract_text(raw: Dict[str, Any]) -> str:
        """Extract the assistant message text from a chat-completion response."""
        try:
            return raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmTransientError(
                f"Unexpected LLM API response shape: {exc}. "
                f"Response keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}"
            ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(exc: urllib.error.HTTPError) -> Optional[float]:
    """Extract the ``Retry-After`` value from an HTTP error response.

    Supports both integer-seconds and HTTP-date formats.

    Returns:
        Number of seconds to wait, or ``None`` if the header is absent or
        cannot be parsed.
    """
    try:
        headers = exc.headers
        if headers is None:
            return None
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if not raw:
            return None
        raw = raw.strip()
        # Integer seconds.
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
        # HTTP-date format (e.g. "Wed, 21 Oct 2015 07:28:00 GMT").
        import email.utils
        import time as _time
        parsed = email.utils.parsedate(raw)
        if parsed is not None:
            wait = _time.mktime(parsed) - _time.time()
            return max(0.0, wait)
    except Exception:  # noqa: BLE001
        pass
    return None


__all__ = [
    "HttpApiBackend",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "_parse_retry_after",
]
