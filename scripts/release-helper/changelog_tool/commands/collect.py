"""``collect`` command — pipeline stages 1, 2, 3, 4.

Stages:
    1. Collect commits from git history.          ← implemented in T3
    2. Identify external contributors.            ← T4
    3. Automatic pre-classification via heuristics. ← T5
    4. LLM analysis of remaining commits (resumable). ← T7

Outputs created by this command:
    .changelog/01_commits.jsonl
    .changelog/01_commits_manifest.json
    .changelog/02_commits_with_contributors.jsonl  (T4)
    .changelog/02_external_contributors.md         (T4)
    .changelog/03_preclassified.jsonl              (T5)
    .changelog/04_llm_classified.jsonl             (T7)
    .changelog/04_llm_classification_state.json    (T7)
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, List

from changelog_tool.git import GitError, collect_commits, utc_now_iso
from changelog_tool.io import (
    compute_sha_checksum,
    ensure_workdir,
    write_json_atomic,
    write_jsonl,
)
from changelog_tool.models import Commit, to_dict

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext
    from changelog_tool.config import Config


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_collect(ctx: "CliContext") -> int:
    """Run the ``collect`` command.

    Loads the resolved configuration and dispatches to :func:`_collect`.
    """
    from changelog_tool.cli import run_with_config

    return run_with_config(ctx, _collect)


# ---------------------------------------------------------------------------
# Stage 1 implementation
# ---------------------------------------------------------------------------


def _collect(ctx: "CliContext", config: "Config") -> int:
    """Orchestrate stages 1–4 of the pipeline.

    Stage 1 is fully implemented here.  Stages 2–4 are stubs that will be
    filled in by T4, T5, and T7 respectively.
    """
    ensure_workdir(config.output.workdir)

    # ------------------------------------------------------------------
    # Stage 1: collect commits from git
    # ------------------------------------------------------------------
    exit_code = _run_stage1(config)
    if exit_code != 0:
        return exit_code

    # ------------------------------------------------------------------
    # Stages 2–4: stubs (implemented in later tasks)
    # ------------------------------------------------------------------
    print()
    print("Stages 2 (contributors), 3 (pre-classify), 4 (LLM): not yet implemented.")
    print()
    print("Next:")
    print("  changelog-tool review")
    return 0


def _run_stage1(config: "Config") -> int:
    """Stage 1: collect commits from git and write artifacts.

    Writes:
        <workdir>/01_commits.jsonl
        <workdir>/01_commits_manifest.json

    Returns 0 on success, non-zero on error.
    """
    print(f"Collecting commits {config.range.from_ref}..{config.range.to_ref}")

    # --- collect ---
    try:
        commits: List[Commit] = collect_commits(
            local_path=config.repo.local_path,
            from_ref=config.range.from_ref,
            to_ref=config.range.to_ref,
            github_url=config.repo.github_url,
        )
    except GitError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # --- invariant: unique SHAs ---
    sha_list = [c.sha for c in commits]
    sha_set = set(sha_list)
    if len(sha_list) != len(sha_set):
        print(f"ERROR: duplicate SHAs {sha_list}", file=sys.stderr)
        return 1

    total = len(commits)
    sha_checksum = compute_sha_checksum(sha_list)

    print(f"\nCommits: {total}")

    # --- write 01_commits.jsonl ---
    commits_path = os.path.join(config.output.workdir, "01_commits.jsonl")
    write_jsonl(commits_path, [to_dict(c) for c in commits])
    print(f"  Written: {commits_path}")

    # --- write 01_commits_manifest.json ---
    manifest = {
        "from_ref": config.range.from_ref,
        "to_ref": config.range.to_ref,
        "total_commits": total,
        "sha_list": sha_list,
        "sha_checksum": sha_checksum,
        "generated_at": utc_now_iso(),
    }
    manifest_path = os.path.join(config.output.workdir, "01_commits_manifest.json")
    write_json_atomic(manifest_path, manifest)
    print(f"  Written: {manifest_path}")

    # --- summary ---
    merge_count = sum(1 for c in commits if c.is_merge_commit)
    print(f"\n  merge commits: {merge_count}")
    print(f"  regular commits: {total - merge_count}")

    return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = ["run_collect"]
