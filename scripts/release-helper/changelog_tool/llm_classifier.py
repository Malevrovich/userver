"""Stage 4: Resumable async LLM classification of commits (Spec §9).

This module implements the full batch-classification pipeline with:

- **Parallel execution** via ``asyncio``: multiple batches are sent to the LLM
  concurrently, bounded by ``llm.max_concurrency``.
- **Rate limiting**: a token-bucket limiter enforces ``llm.max_rps`` (requests
  per second).  Set ``max_rps = 0`` to disable rate limiting.
- **Resumability**: state is persisted to ``04_llm_classification_state.json``
  after every successful batch (Spec §9.9).
- **Retry-once**: on ``LlmTransientError`` or ``LlmResponseError``, the batch
  is retried once; on second failure all commits in the batch receive fallback
  ``unclear`` classification (Spec §9.10).
- **Oversized-commit fallback**: commits whose single-commit prompt exceeds
  ``llm.max_prompt_chars`` receive ``unclear`` immediately (Spec §9.7, §14.10).

Outputs
-------
``04_llm_classified.jsonl``
    One line per commit that was sent to the LLM (or received fallback).

``04_llm_classification_state.json``
    Resumable state file.  Written atomically after each batch.

Spec references
---------------
§9.4  — LlmClassification format
§9.7  — batching and oversized-commit fallback
§9.9  — resumable state file
§9.10 — invalid JSON handling and retry
§9.11 — prompt contract
§9.12 — per-batch invariants
§14.3 — LLM classification failure
§14.10 — single commit too large for LLM
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from changelog_tool.io import read_json, write_json_atomic, write_jsonl
from changelog_tool.llm.base import LlmClient
from changelog_tool.llm.context import commit_to_context, maybe_attach_diff
from changelog_tool.llm.parsing import parse_classification_response
from changelog_tool.llm.retry import TokenBucket, execute_with_retry
from changelog_tool.llm.prompts import build_classification_prompt, estimate_chars
from changelog_tool.models import (
    Commit,
    Confidence,
    LlmCategory,
    LlmClassification,
)

# ---------------------------------------------------------------------------
# Batch status constants (Spec §9.9)
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Attention flags.
ATTENTION_TOO_LARGE = "commit_too_large_for_llm"
ATTENTION_LLM_FALLBACK = "llm_fallback_unclear"

# ---------------------------------------------------------------------------
# Fallback classification helpers
# ---------------------------------------------------------------------------


def _fallback_classification(comment: str) -> LlmClassification:
    return LlmClassification(
        category=LlmCategory.UNCLEAR,
        candidate_for_changelog=False,
        changelog_line=None,
        comment=comment,
        confidence=Confidence.LOW,
    )


_OVERSIZED_FALLBACK = _fallback_classification(
    "Commit is too large to fit into LLM prompt as an atomic unit."
)
_INVALID_JSON_FALLBACK = _fallback_classification(
    "LLM returned invalid JSON for this batch."
)


# ---------------------------------------------------------------------------
# Batch building (Spec §9.7)
# ---------------------------------------------------------------------------


def _build_batches(
    commits: List[Commit],
    batch_size: int,
    max_prompt_chars: int,
    repo_path: str,
    include_diff: bool,
    diff_max_chars: int,
) -> Tuple[List[List[Commit]], List[Commit]]:
    """Split *commits* into batches; identify oversized single commits."""
    batches: List[List[Commit]] = []
    oversized: List[Commit] = []

    current_batch: List[Commit] = []
    current_chars = 0

    for commit in commits:
        ctx = commit_to_context(commit)
        if include_diff:
            maybe_attach_diff(ctx, commit, repo_path, diff_max_chars)

        single_req = build_classification_prompt([ctx])
        single_chars = estimate_chars(single_req)

        if single_chars > max_prompt_chars:
            oversized.append(commit)
            continue

        if current_batch and (
            len(current_batch) >= batch_size
            or current_chars + single_chars > max_prompt_chars
        ):
            batches.append(current_batch)
            current_batch = []
            current_chars = 0

        current_batch.append(commit)
        current_chars += single_chars

    if current_batch:
        batches.append(current_batch)

    return batches, oversized


# ---------------------------------------------------------------------------
# State file helpers (Spec §9.9)
# ---------------------------------------------------------------------------


def _load_state(state_path: str) -> Dict[str, Any]:
    if not os.path.exists(state_path):
        return {"operation": "llm_classification", "batches": {}}
    try:
        return read_json(state_path)
    except Exception:  # noqa: BLE001
        return {"operation": "llm_classification", "batches": {}}


def _save_state(state_path: str, state: Dict[str, Any]) -> None:
    write_json_atomic(state_path, state)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _llm_classification_to_dict(cls: LlmClassification) -> Dict[str, Any]:
    return {
        "category": cls.category.value,
        "candidate_for_changelog": cls.candidate_for_changelog,
        "changelog_line": cls.changelog_line,
        "comment": cls.comment,
        "confidence": cls.confidence.value,
    }


def _llm_classification_from_dict(d: Dict[str, Any]) -> LlmClassification:
    return LlmClassification(
        category=LlmCategory(d.get("category", "unclear")),
        candidate_for_changelog=bool(d.get("candidate_for_changelog", False)),
        changelog_line=d.get("changelog_line"),
        comment=d.get("comment"),
        confidence=Confidence(d.get("confidence", "low")),
    )


def _load_done_results(
    state: Dict[str, Any],
    classifications: Dict[str, LlmClassification],
) -> None:
    for batch_data in state.get("batches", {}).values():
        if batch_data.get("status") != STATUS_DONE:
            continue
        for sha, raw in batch_data.get("results", {}).items():
            classifications[sha] = _llm_classification_from_dict(raw)


def _write_classified_jsonl(
    path: str,
    classifications: Dict[str, LlmClassification],
) -> None:
    records = [
        {"sha": sha, "llm_classification": _llm_classification_to_dict(cls)}
        for sha, cls in classifications.items()
    ]
    write_jsonl(path, records)


# ---------------------------------------------------------------------------
# Async batch worker
# ---------------------------------------------------------------------------


async def _process_batch(
    batch_idx: int,
    batch: List[Commit],
    client: LlmClient,
    state: Dict[str, Any],
    state_path: str,
    state_lock: asyncio.Lock,
    classifications: Dict[str, LlmClassification],
    classifications_lock: asyncio.Lock,
    semaphore: asyncio.Semaphore,
    rate_limiter: TokenBucket,
    repo_path: str,
    include_diff: bool,
    diff_max_chars: int,
    verbose: bool,
    max_rps: float = 0.0,
    max_429_retries: int = 5,
) -> Tuple[bool, bool]:
    """Process a single batch asynchronously.

    Returns:
        (was_done, was_failed) — both False if skipped (already done).
    """
    batch_id = f"batch-{batch_idx + 1:04d}"
    sha_list = [c.sha for c in batch]

    # Check if already done (under lock to avoid races).
    async with state_lock:
        existing = state["batches"].get(batch_id, {})
        if existing.get("status") == STATUS_DONE:
            if verbose:
                print(f"  {batch_id}: already done ({len(sha_list)} commits), skipping.")
            return False, False

        # Mark in_progress.
        state["batches"][batch_id] = {
            "status": STATUS_IN_PROGRESS,
            "sha_list": sha_list,
            "attempts": existing.get("attempts", 0),
        }
        _save_state(state_path, state)

    print(f"  {batch_id}: processing {len(sha_list)} commit(s)...")

    # Build context list.
    contexts = []
    for commit in batch:
        ctx = commit_to_context(commit)
        if include_diff:
            maybe_attach_diff(ctx, commit, repo_path, diff_max_chars)
        contexts.append(ctx)

    request = build_classification_prompt(contexts)

    # Acquire rate-limit token and concurrency slot.
    await rate_limiter.acquire()
    
    async def _do_classify() -> Dict[str, LlmClassification]:
        response = await client.async_complete(request)
        result_list = parse_classification_response(response.text, sha_list)
        return {sha: cls for sha, cls in zip(sha_list, result_list)}

    async with semaphore:
        result_classifications, error = await execute_with_retry(
            operation=_do_classify,
            batch_id=batch_id,
            verbose=verbose,
            max_rps=max_rps,
            max_429_retries=max_429_retries,
        )

    async with state_lock:
        attempts = state["batches"][batch_id].get("attempts", 0) + 1

        if result_classifications is not None:
            async with classifications_lock:
                classifications.update(result_classifications)
            state["batches"][batch_id] = {
                "status": STATUS_DONE,
                "sha_list": sha_list,
                "attempts": attempts,
                "results": {
                    sha: _llm_classification_to_dict(cls)
                    for sha, cls in result_classifications.items()
                },
            }
            print(f"  {batch_id}: done.")
            _save_state(state_path, state)
            return True, False
        else:
            fallback = {sha: _INVALID_JSON_FALLBACK for sha in sha_list}
            async with classifications_lock:
                classifications.update(fallback)
            state["batches"][batch_id] = {
                "status": STATUS_DONE,
                "sha_list": sha_list,
                "attempts": attempts,
                "error": error,
                "fallback": True,
                "results": {
                    sha: _llm_classification_to_dict(_INVALID_JSON_FALLBACK)
                    for sha in sha_list
                },
            }
            print(
                f"  {batch_id}: failed after {attempts} attempt(s), "
                f"fallback unclear applied."
            )
            _save_state(state_path, state)
            return False, True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ClassificationResult:
    """Summary returned by :func:`run_llm_classification`."""

    total_sent: int
    batches_done: int
    batches_failed: int
    batches_skipped_already_done: int
    oversized_commits: int
    classifications: Dict[str, LlmClassification]


def run_llm_classification(
    commits: List[Commit],
    client: LlmClient,
    state_path: str,
    classified_path: str,
    *,
    batch_size: int,
    max_prompt_chars: int,
    from_ref: str,
    to_ref: str,
    repo_path: str = ".",
    include_diff: bool = False,
    diff_max_chars: int = 20000,
    max_rps: float = 0.0,
    max_concurrency: int = 1,
    max_429_retries: int = 5,
    verbose: bool = False,
) -> ClassificationResult:
    """Run resumable async LLM classification for *commits* (Spec §9).

    Only commits with ``auto_classification.send_to_llm == true`` are
    processed.  All others are ignored (handled by stage 5).

    Args:
        commits:           Full list of pre-classified commits (stage-3 output).
        client:            LLM backend to use.
        state_path:        Path to ``04_llm_classification_state.json``.
        classified_path:   Path to ``04_llm_classified.jsonl``.
        batch_size:        Max commits per LLM batch.
        max_prompt_chars:  Max chars per prompt.
        from_ref:          Git ref for the start of the range.
        to_ref:            Git ref for the end of the range.
        repo_path:         Path to the local git repo.
        include_diff:      Whether to attach ``git show`` diffs.
        diff_max_chars:    Max chars for each diff.
        max_rps:           Target requests per second (0 = unlimited).
        max_concurrency:   Max concurrent LLM requests.
        max_429_retries:   Max times to retry a 429 before falling back to unclear.
        verbose:           Print extra detail.

    Returns:
        :class:`ClassificationResult` with counts and the full SHA→classification
        mapping.
    """
    return asyncio.run(
        _run_async(
            commits=commits,
            client=client,
            state_path=state_path,
            classified_path=classified_path,
            batch_size=batch_size,
            max_prompt_chars=max_prompt_chars,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_path=repo_path,
            include_diff=include_diff,
            diff_max_chars=diff_max_chars,
            max_rps=max_rps,
            max_concurrency=max_concurrency,
            max_429_retries=max_429_retries,
            verbose=verbose,
        )
    )


async def _run_async(
    commits: List[Commit],
    client: LlmClient,
    state_path: str,
    classified_path: str,
    *,
    batch_size: int,
    max_prompt_chars: int,
    from_ref: str,
    to_ref: str,
    repo_path: str,
    include_diff: bool,
    diff_max_chars: int,
    max_rps: float,
    max_concurrency: int,
    max_429_retries: int,
    verbose: bool,
) -> ClassificationResult:
    """Async implementation of :func:`run_llm_classification`."""

    to_classify = [
        c for c in commits
        if c.auto_classification is not None and c.auto_classification.send_to_llm
    ]

    if not to_classify:
        print("  No commits require LLM analysis.")
        return ClassificationResult(
            total_sent=0,
            batches_done=0,
            batches_failed=0,
            batches_skipped_already_done=0,
            oversized_commits=0,
            classifications={},
        )

    batches, oversized = _build_batches(
        to_classify,
        batch_size=batch_size,
        max_prompt_chars=max_prompt_chars,
        repo_path=repo_path,
        include_diff=include_diff,
        diff_max_chars=diff_max_chars,
    )

    state = _load_state(state_path)
    state["operation"] = "llm_classification"
    state["from_ref"] = from_ref
    state["to_ref"] = to_ref
    if "batches" not in state:
        state["batches"] = {}

    classifications: Dict[str, LlmClassification] = {}
    _load_done_results(state, classifications)

    for commit in oversized:
        classifications[commit.sha] = _OVERSIZED_FALLBACK
        print(
            f"  ⚠  {commit.short_sha} — too large for LLM prompt, "
            f"marked unclear (attention: {ATTENTION_TOO_LARGE})"
        )

    if max_rps > 0:
        print(f"  Rate limit: {max_rps} RPS, concurrency: {max_concurrency}")
    else:
        print(f"  Concurrency: {max_concurrency} (no rate limit)")

    semaphore = asyncio.Semaphore(max_concurrency)
    rate_limiter = TokenBucket(max_rps)
    state_lock = asyncio.Lock()
    classifications_lock = asyncio.Lock()

    tasks = [
        _process_batch(
            batch_idx=idx,
            batch=batch,
            client=client,
            state=state,
            state_path=state_path,
            state_lock=state_lock,
            classifications=classifications,
            classifications_lock=classifications_lock,
            semaphore=semaphore,
            rate_limiter=rate_limiter,
            repo_path=repo_path,
            include_diff=include_diff,
            diff_max_chars=diff_max_chars,
            verbose=verbose,
            max_rps=max_rps,
            max_429_retries=max_429_retries,
        )
        for idx, batch in enumerate(batches)
    ]

    results = await asyncio.gather(*tasks)

    batches_done = sum(1 for done, _ in results if done)
    batches_failed = sum(1 for _, failed in results if failed)
    batches_skipped = sum(1 for done, failed in results if not done and not failed)

    _write_classified_jsonl(classified_path, classifications)

    total_sent = len(to_classify)
    print(
        f"\n  LLM classification complete:"
        f"\n    commits sent:      {total_sent}"
        f"\n    oversized:         {len(oversized)}"
        f"\n    batches done:      {batches_done}"
        f"\n    batches failed:    {batches_failed}"
        f"\n    batches skipped:   {batches_skipped} (already done)"
    )

    return ClassificationResult(
        total_sent=total_sent,
        batches_done=batches_done,
        batches_failed=batches_failed,
        batches_skipped_already_done=batches_skipped,
        oversized_commits=len(oversized),
        classifications=classifications,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "run_llm_classification",
    "ClassificationResult",
    "ATTENTION_TOO_LARGE",
    "ATTENTION_LLM_FALLBACK",
]
