"""``collect`` command — pipeline stages 1, 2, 3, 4.

Stages:
    1. Collect commits from git history.              ← T3
    2. Identify external contributors.                ← T4
    3. Automatic pre-classification via heuristics.   ← T5 (this file)
    4. LLM analysis of remaining commits (resumable). ← T7

Outputs created by this command:
    .changelog/01_commits.jsonl
    .changelog/01_commits_manifest.json
    .changelog/02_commits_with_contributors.jsonl
    .changelog/02_external_contributors.md
    .changelog/03_preclassified.jsonl
    .changelog/04_llm_classified.jsonl             (T7)
    .changelog/04_llm_classification_state.json    (T7)
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Dict, List, Set

from changelog_tool.classifier import preclassify
from changelog_tool.git import GitError, collect_commits, utc_now_iso
from changelog_tool.io import (
    compute_sha_checksum,
    ensure_workdir,
    read_jsonl,
    write_json_atomic,
    write_jsonl,
)
from changelog_tool.models import Commit, commit_from_dict, to_dict

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
# Orchestrator
# ---------------------------------------------------------------------------


def _collect(ctx: "CliContext", config: "Config") -> int:
    """Orchestrate stages 1–4 of the pipeline."""
    ensure_workdir(config.output.workdir)

    # ------------------------------------------------------------------
    # Stage 1: collect commits from git
    # ------------------------------------------------------------------
    exit_code = _run_stage1(config)
    if exit_code != 0:
        return exit_code

    # ------------------------------------------------------------------
    # Stage 2: identify external contributors
    # ------------------------------------------------------------------
    exit_code = _run_stage2(config)
    if exit_code != 0:
        return exit_code

    # ------------------------------------------------------------------
    # Stage 3: heuristic pre-classification
    # ------------------------------------------------------------------
    exit_code = _run_stage3(config)
    if exit_code != 0:
        return exit_code

    # ------------------------------------------------------------------
    # Stage 4: LLM classification (stub — implemented in T7)
    # ------------------------------------------------------------------
    print()
    print("Stage 4 (LLM): not yet implemented.")
    print()
    print("Next:")
    print("  changelog-tool review")
    return 0


# ---------------------------------------------------------------------------
# Stage 1
# ---------------------------------------------------------------------------


def _run_stage1(config: "Config") -> int:
    """Stage 1: collect commits from git and write artifacts.

    Writes:
        <workdir>/01_commits.jsonl
        <workdir>/01_commits_manifest.json

    Returns 0 on success, non-zero on error.
    """
    print(f"Collecting commits {config.range.from_ref}..{config.range.to_ref}")

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

    # invariant: unique SHAs
    sha_list = [c.sha for c in commits]
    sha_set = set(sha_list)
    if len(sha_list) != len(sha_set):
        seen: set = set()
        dupes = [s for s in sha_list if s in seen or seen.add(s)]  # type: ignore[func-returns-value]
        print("ERROR: duplicate SHA found", file=sys.stderr)
        for s in dupes:
            print(f"  - {s}", file=sys.stderr)
        return 1

    total = len(commits)
    sha_checksum = compute_sha_checksum(sha_list)

    print(f"\nCommits: {total}")

    commits_path = os.path.join(config.output.workdir, "01_commits.jsonl")
    write_jsonl(commits_path, [to_dict(c) for c in commits])
    print(f"  Written: {commits_path}")

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

    merge_count = sum(1 for c in commits if c.is_merge_commit)
    print(f"\n  merge commits:   {merge_count}")
    print(f"  regular commits: {total - merge_count}")

    return 0


# ---------------------------------------------------------------------------
# Stage 2
# ---------------------------------------------------------------------------


def _run_stage2(config: "Config") -> int:
    """Stage 2: mark is_external on each commit by comparing email to core_team.emails.

    Reads:
        <workdir>/01_commits.jsonl

    Writes:
        <workdir>/02_commits_with_contributors.jsonl
        <workdir>/02_external_contributors.md

    Returns 0 on success, non-zero on error.
    """
    print("\nIdentifying external contributors...")

    commits_path = os.path.join(config.output.workdir, "01_commits.jsonl")
    try:
        commits = [commit_from_dict(d) for d in read_jsonl(commits_path)]
    except (OSError, Exception) as exc:
        print(f"ERROR: could not read {commits_path}: {exc}", file=sys.stderr)
        return 1

    core_emails: Set[str] = {e.lower() for e in config.core_team.emails}

    for commit in commits:
        commit.is_external = commit.author_email.lower() not in core_emails
        for ca in commit.co_authors:
            ca.is_external = ca.email.lower() not in core_emails

    workdir = config.output.workdir

    # 02_commits_with_contributors.jsonl
    enriched_path = os.path.join(workdir, "02_commits_with_contributors.jsonl")
    write_jsonl(enriched_path, [to_dict(c) for c in commits])
    print(f"  Written: {enriched_path}")

    # 02_external_contributors.md
    md_path = os.path.join(workdir, "02_external_contributors.md")
    _write_external_contributors_md(commits, config.repo.github_url, md_path)
    print(f"  Written: {md_path}")

    external_count = sum(
        1 for c in commits
        if c.is_external or any(ca.is_external for ca in c.co_authors)
    )
    print(f"\n  External contributors: {external_count}")
    return 0


def _write_external_contributors_md(
    commits: List[Commit], github_url: str, path: str
) -> None:
    """Write 02_external_contributors.md."""
    base_url = github_url.rstrip("/")
    # identity → list of (short_sha, commit_url, subject, role)
    by_identity: Dict[str, list] = {}

    for commit in commits:
        if commit.is_external:
            identity = f"{commit.author_name} <{commit.author_email}>"
            by_identity.setdefault(identity, []).append(
                (commit.short_sha, f"{base_url}/commit/{commit.sha}", commit.subject, "author")
            )
        for ca in commit.co_authors:
            if ca.is_external:
                identity = f"{ca.name} <{ca.email}>"
                by_identity.setdefault(identity, []).append(
                    (commit.short_sha, f"{base_url}/commit/{commit.sha}", commit.subject, "co-author")
                )

    lines = ["# External Contributors\n"]
    if not by_identity:
        lines.append("\nNo external contributors found.\n")
    else:
        for identity in sorted(by_identity):
            lines.append(f"\n## {identity}\n")
            for short_sha, url, subject, role in by_identity[identity]:
                lines.append(f"- **{role}** — [`{short_sha}`]({url}) {subject}\n")

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


# ---------------------------------------------------------------------------
# Stage 3
# ---------------------------------------------------------------------------


def _run_stage3(config: "Config") -> int:
    """Stage 3: heuristic pre-classification.

    Reads:
        <workdir>/02_commits_with_contributors.jsonl

    Writes:
        <workdir>/03_preclassified.jsonl

    Returns 0 on success, non-zero on error.
    """
    print("\nPre-classifying commits...")

    enriched_path = os.path.join(config.output.workdir, "02_commits_with_contributors.jsonl")
    try:
        commits = [commit_from_dict(d) for d in read_jsonl(enriched_path)]
    except (OSError, Exception) as exc:
        print(f"ERROR: could not read {enriched_path}: {exc}", file=sys.stderr)
        return 1

    preclassify(
        commits=commits,
        small_commit_threshold=config.thresholds.small_commit,
        bugfix_skip_threshold=config.thresholds.bugfix_skip,
    )

    # Invariant: every commit must have auto_classification
    for commit in commits:
        if commit.auto_classification is None:
            print(f"ERROR: commit {commit.sha} has no auto_classification", file=sys.stderr)
            return 1

    out_path = os.path.join(config.output.workdir, "03_preclassified.jsonl")
    write_jsonl(out_path, [to_dict(c) for c in commits])
    print(f"  Written: {out_path}")

    # Summary
    send_to_llm = sum(1 for c in commits if c.auto_classification.send_to_llm)  # type: ignore[union-attr]
    skipped = len(commits) - send_to_llm
    print(f"\n  Heuristics filtered: {skipped}")
    print(f"  Sent to LLM:         {send_to_llm}")

    return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = ["run_collect"]
