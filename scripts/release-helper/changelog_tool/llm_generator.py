"""Stage 6: Resumable async LLM generation of missing CHANGELOG lines (Spec §11.7).

This module implements the generation of missing ``changelog_line`` values for
commits that are selected for the CHANGELOG but lack a line.

Outputs
-------
``06_changelog_line_generation_state.json``
    Resumable state file. Written atomically after each item.

Spec references
---------------
§11.7 — Generating missing CHANGELOG lines
§11.8 — Prompt for generating a missing CHANGELOG line
§14.9 — CHANGELOG line generation failure
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from changelog_tool.io import compute_sha_checksum, read_json, write_json_atomic
from changelog_tool.llm.base import LlmClient
from changelog_tool.llm.context import commit_to_context
from changelog_tool.llm.errors import LlmResponseError
from changelog_tool.llm.prompts import build_missing_line_prompt
from changelog_tool.llm.retry import TokenBucket, execute_with_retry
from changelog_tool.models import VerifiedCommit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

TODO_FALLBACK_PREFIX = "[TODO: write changelog line]"


# ---------------------------------------------------------------------------
# Prompt Building (Spec §11.8)
# ---------------------------------------------------------------------------


def _clean_response(text: str) -> str:
    """Extract the useful text from the LLM response (Spec §11.8)."""
    text = text.strip()

    # Remove markdown code blocks if present
    if text.startswith("```") and text.endswith("```"):
        lines = text.split("\n")
        if len(lines) >= 2:
            # Remove first and last line
            text = "\n".join(lines[1:-1]).strip()

    # Remove leading bullets if the LLM ignored instructions
    if text.startswith("- "):
        text = text[2:].strip()
    elif text.startswith("* "):
        text = text[2:].strip()

    # Remove quotes if the LLM wrapped the line in quotes
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()

    # Take only the first line if it returned multiple
    text = text.split("\n")[0].strip()

    return text


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------


def _load_state(state_path: str) -> Dict[str, Any]:
    if not os.path.exists(state_path):
        return {"operation": "changelog_line_generation", "items": {}}
    try:
        return read_json(state_path)
    except Exception:  # noqa: BLE001
        return {"operation": "changelog_line_generation", "items": {}}


def _save_state(state_path: str, state: Dict[str, Any]) -> None:
    write_json_atomic(state_path, state)


# ---------------------------------------------------------------------------
# Async worker
# ---------------------------------------------------------------------------


async def _process_item(
    commit: VerifiedCommit,
    client: LlmClient,
    state: Dict[str, Any],
    state_path: str,
    state_lock: asyncio.Lock,
    semaphore: asyncio.Semaphore,
    rate_limiter: TokenBucket,
    max_rps: float = 0.0,
    max_429_retries: int = 5,
) -> Tuple[bool, bool]:
    """Process a single commit asynchronously.

    Returns:
        (was_done, was_failed) — both False if skipped (already done).
    """
    sha = commit.sha

    async with state_lock:
        existing = state["items"].get(sha, {})
        if existing.get("status") == STATUS_DONE:
            # Apply the already generated line to the commit object
            commit.changelog_line = existing.get("changelog_line")
            return False, False

        state["items"][sha] = {
            "status": STATUS_IN_PROGRESS,
            "attempts": existing.get("attempts", 0),
        }
        _save_state(state_path, state)

    # Convert VerifiedCommit to the context dict expected by the prompt builder
    # (VerifiedCommit has the same fields as Commit for this purpose)
    ctx_dict = commit_to_context(commit)  # type: ignore[arg-type]
    request = build_missing_line_prompt(
        context=ctx_dict,
        category=commit.category.value,
        maintainer_comment=commit.classification_comment,
    )

    await rate_limiter.acquire()

    async def _do_generate() -> str:
        response = await client.async_complete(request)
        line = _clean_response(response.text)
        if not line:
            raise LlmResponseError("LLM returned empty response")
        return line

    async with semaphore:
        line, error = await execute_with_retry(
            operation=_do_generate,
            batch_id=sha[:8],
            verbose=False,
            max_rps=max_rps,
            max_429_retries=max_429_retries,
        )

    async with state_lock:
        attempts = state["items"][sha].get("attempts", 0) + 1

        if line is not None:
            commit.changelog_line = line
            state["items"][sha] = {
                "status": STATUS_DONE,
                "attempts": attempts,
                "changelog_line": line,
            }
            _save_state(state_path, state)
            return True, False
        else:
            # Fallback (Spec §11.7)
            fallback_line = f"{TODO_FALLBACK_PREFIX} {commit.subject}"
            commit.changelog_line = fallback_line
            state["items"][sha] = {
                "status": STATUS_FAILED,
                "attempts": attempts,
                "error": error,
                "changelog_line": fallback_line,
            }
            _save_state(state_path, state)
            return False, True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class GenerationResult:
    """Summary returned by :func:`run_line_generation`."""

    total_needed: int
    items_done: int
    items_failed: int
    items_skipped_already_done: int


def run_line_generation(
    commits: List[VerifiedCommit],
    client: LlmClient,
    state_path: str,
    *,
    max_rps: float = 0.0,
    max_concurrency: int = 5,
    max_429_retries: int = 5,
) -> GenerationResult:
    """Run resumable async LLM generation for missing CHANGELOG lines.

    Mutates the ``changelog_line`` field of the commits in-place.

    Args:
        commits:         List of verified commits (after overrides applied).
        client:          LLM backend to use.
        state_path:      Path to ``06_changelog_line_generation_state.json``.
        max_concurrency: Max concurrent LLM requests.
        max_429_retries: Max times to retry a 429 before falling back.

    Returns:
        :class:`GenerationResult` with counts.
    """
    return asyncio.run(
        _run_async(
            commits=commits,
            client=client,
            state_path=state_path,
            max_rps=max_rps,
            max_concurrency=max_concurrency,
            max_429_retries=max_429_retries,
        )
    )


async def _run_async(
    commits: List[VerifiedCommit],
    client: LlmClient,
    state_path: str,
    max_rps: float,
    max_concurrency: int,
    max_429_retries: int,
) -> GenerationResult:
    # Select candidates that need a line
    to_process = [
        c for c in commits
        if c.candidate_for_changelog and not c.changelog_line
    ]

    if not to_process:
        return GenerationResult(0, 0, 0, 0)

    state = _load_state(state_path)
    state["operation"] = "changelog_line_generation"
    state["input_checksum"] = compute_sha_checksum([c.sha for c in to_process])
    if "items" not in state:
        state["items"] = {}

    semaphore = asyncio.Semaphore(max_concurrency)
    rate_limiter = TokenBucket(max_rps)
    state_lock = asyncio.Lock()

    tasks = [
        _process_item(
            commit=commit,
            client=client,
            state=state,
            state_path=state_path,
            state_lock=state_lock,
            semaphore=semaphore,
            rate_limiter=rate_limiter,
            max_rps=max_rps,
            max_429_retries=max_429_retries,
        )
        for commit in to_process
    ]

    results = await asyncio.gather(*tasks)

    items_done = sum(1 for done, _ in results if done)
    items_failed = sum(1 for _, failed in results if failed)
    items_skipped = sum(1 for done, failed in results if not done and not failed)

    return GenerationResult(
        total_needed=len(to_process),
        items_done=items_done,
        items_failed=items_failed,
        items_skipped_already_done=items_skipped,
    )


__all__ = [
    "GenerationResult",
    "run_line_generation",
    "TODO_FALLBACK_PREFIX",
]
