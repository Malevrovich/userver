"""Deterministic fake LLM backend for tests and dry runs.

Returns canned responses keyed by SHA (for classification) or by a
user-supplied callable.  Never makes network calls.

Usage in tests::

    from changelog_tool.llm.backends.fake import FakeBackend

    backend = FakeBackend(
        responses={
            "abc123": {
                "category": "feature",
                "candidate_for_changelog": True,
                "changelog_line": "Added X.",
                "comment": "New feature.",
                "confidence": "high",
            }
        }
    )
    client = backend
    response = client.complete(request)

If a SHA is not in *responses*, the backend returns a default ``unclear``
classification for that SHA.

For missing-line prompts (which contain a single SHA in ``metadata["sha"]``),
the same mapping is used; if absent, a generic line is returned.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from changelog_tool.llm.base import LlmClient, LlmRequest, LlmResponse

# Default classification returned when a SHA is not in the canned map.
_DEFAULT_ITEM = {
    "category": "unclear",
    "candidate_for_changelog": False,
    "changelog_line": None,
    "comment": "FakeBackend default: no canned response for this SHA.",
    "confidence": "low",
}


class FakeBackend(LlmClient):
    """Deterministic LLM backend for offline tests and dry runs.

    Args:
        responses:       Optional dict mapping SHA → classification item dict.
                         Used for both classification and missing-line prompts.
        response_fn:     Optional callable ``(request) -> str`` that produces
                         the raw response text.  Takes precedence over
                         *responses* when provided.
        default_line:    Default CHANGELOG line returned for missing-line
                         prompts when the SHA is not in *responses*.
    """

    def __init__(
        self,
        *,
        responses: Optional[Dict[str, Dict[str, Any]]] = None,
        response_fn: Optional[Callable[[LlmRequest], str]] = None,
        default_line: str = "Fake changelog line.",
    ) -> None:
        self._responses: Dict[str, Dict[str, Any]] = responses or {}
        self._response_fn = response_fn
        self._default_line = default_line
        self.call_count = 0
        self.last_request: Optional[LlmRequest] = None

    # ------------------------------------------------------------------
    # LlmClient interface
    # ------------------------------------------------------------------

    def complete(self, request: LlmRequest) -> LlmResponse:
        """Return a deterministic canned response for *request*.

        Args:
            request: The prompt (metadata is used to look up canned data).

        Returns:
            :class:`~changelog_tool.llm.base.LlmResponse` with JSON text.
        """
        self.call_count += 1
        self.last_request = request

        if self._response_fn is not None:
            text = self._response_fn(request)
            return LlmResponse(text=text, backend="fake")

        # Determine whether this is a classification or missing-line prompt.
        sha_list: List[str] = request.metadata.get("sha_list", [])
        single_sha: str = request.metadata.get("sha", "")

        if sha_list:
            text = self._build_classification_response(sha_list)
        elif single_sha:
            text = self._build_missing_line_response(single_sha)
        else:
            # Fallback: return an empty classification response.
            text = json.dumps({"items": [], "stats": {"input_count": 0, "output_count": 0}})

        return LlmResponse(text=text, backend="fake")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_classification_response(self, sha_list: List[str]) -> str:
        items = []
        for sha in sha_list:
            item = dict(self._responses.get(sha, _DEFAULT_ITEM))
            item["sha"] = sha
            items.append(item)
        payload = {
            "items": items,
            "stats": {
                "input_count": len(sha_list),
                "output_count": len(items),
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    def _build_missing_line_response(self, sha: str) -> str:
        canned = self._responses.get(sha, {})
        line = canned.get("changelog_line") or self._default_line
        return line


__all__ = ["FakeBackend"]
