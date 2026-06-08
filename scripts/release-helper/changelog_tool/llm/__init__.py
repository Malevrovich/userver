"""LLM abstraction layer for the CHANGELOG preparation tool.

Public API re-exported from this package:

Interface
---------
- :class:`~changelog_tool.llm.base.LlmClient`
- :class:`~changelog_tool.llm.base.LlmRequest`
- :class:`~changelog_tool.llm.base.LlmResponse`

Errors
------
- :class:`~changelog_tool.llm.errors.LlmError`
- :class:`~changelog_tool.llm.errors.LlmTransientError`
- :class:`~changelog_tool.llm.errors.LlmResponseError`

Factory
-------
- :func:`~changelog_tool.llm.factory.build_client`

Prompt builders
---------------
- :func:`~changelog_tool.llm.prompts.build_classification_prompt`
- :func:`~changelog_tool.llm.prompts.build_missing_line_prompt`
- :func:`~changelog_tool.llm.prompts.estimate_chars`

Parsing
-------
- :func:`~changelog_tool.llm.parsing.extract_json_block`
- :func:`~changelog_tool.llm.parsing.parse_classification_response`
- :func:`~changelog_tool.llm.parsing.parse_missing_line_response`

Context
-------
- :func:`~changelog_tool.llm.context.commit_to_context`
- :func:`~changelog_tool.llm.context.maybe_attach_diff`
"""

from changelog_tool.llm.base import LlmClient, LlmRequest, LlmResponse
from changelog_tool.llm.context import commit_to_context, maybe_attach_diff
from changelog_tool.llm.errors import LlmError, LlmResponseError, LlmTransientError
from changelog_tool.llm.factory import build_client
from changelog_tool.llm.parsing import (
    extract_json_block,
    parse_classification_response,
    parse_missing_line_response,
)
from changelog_tool.llm.prompts import (
    build_classification_prompt,
    build_missing_line_prompt,
    estimate_chars,
)

__all__ = [
    # Interface
    "LlmClient",
    "LlmRequest",
    "LlmResponse",
    # Errors
    "LlmError",
    "LlmTransientError",
    "LlmResponseError",
    # Factory
    "build_client",
    # Prompt builders
    "build_classification_prompt",
    "build_missing_line_prompt",
    "estimate_chars",
    # Parsing
    "extract_json_block",
    "parse_classification_response",
    "parse_missing_line_response",
    # Context
    "commit_to_context",
    "maybe_attach_diff",
]
