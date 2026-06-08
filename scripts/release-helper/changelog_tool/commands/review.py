"""``review`` command — pipeline stage 5.

Stage 5:
    5.1. Verify that no commit was lost (machine checks).
    5.2. Prepare human-readable review data and the overrides file.

Outputs created by this command:
    .changelog/05_verified_classification.jsonl
    .changelog/05_verification_report.json
    .changelog/05_review_report.md      (stage 5.2 — T9)
    .changelog/05_review_overrides.yaml (stage 5.2 — T9)

Spec references
---------------
§10.1 — Stage 5.1: Verification
§13.2 — ``review`` CLI contract
§14.1 — Lost SHA error
§14.2 — Duplicate SHA error
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from changelog_tool.commands import print_config_summary

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext
    from changelog_tool.config import Config

# Exit codes (mirrors cli.py constants to avoid circular import).
_EXIT_OK = 0
_EXIT_VERIFICATION_FAILED = 1


def run_review(ctx: "CliContext") -> int:
    """Run the ``review`` command.

    Loads the resolved configuration and dispatches to :func:`_review`.
    """

    from changelog_tool.cli import run_with_config

    return run_with_config(ctx, _review)


def _review(ctx: "CliContext", config: "Config") -> int:
    """Implement the ``review`` command body.

    Stage 5.1: verification and merge.
    Stage 5.2: review report and overrides (T9 — not yet implemented).
    """
    from changelog_tool.io import ensure_workdir
    from changelog_tool.verifier import VerificationError, run_verification

    workdir = config.output.workdir
    ensure_workdir(workdir)

    # --- Input paths ---
    manifest_path = os.path.join(workdir, "01_commits_manifest.json")
    preclassified_path = os.path.join(workdir, "03_preclassified.jsonl")
    llm_classified_path = os.path.join(workdir, "04_llm_classified.jsonl")

    # --- Output paths ---
    verified_path = os.path.join(workdir, "05_verified_classification.jsonl")
    report_path = os.path.join(workdir, "05_verification_report.json")

    # Check required inputs exist.
    for path in (manifest_path, preclassified_path):
        if not os.path.exists(path):
            print(
                f"ERROR: required input file not found: {path}\n"
                "Run `changelog-tool collect` first.",
                file=sys.stderr,
            )
            return _EXIT_VERIFICATION_FAILED

    # --- Stage 5.1: Verification ---
    try:
        result = run_verification(
            manifest_path=manifest_path,
            preclassified_path=preclassified_path,
            llm_classified_path=llm_classified_path,
            verified_path=verified_path,
            report_path=report_path,
        )
    except VerificationError as exc:
        print(str(exc), file=sys.stderr)
        return _EXIT_VERIFICATION_FAILED
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return _EXIT_VERIFICATION_FAILED

    # --- Print summary (Spec §13.2) ---
    checks = result.checks
    stats = result.stats

    print("Verification: PASSED")
    print()
    print("Generated:")
    print(f"  {verified_path}")
    print(f"  {report_path}")
    print()
    print("Stats:")
    print(f"  total commits:          {result.manifest.get('total_commits', len(result.verified))}")
    print(f"  sent to LLM:            {stats.get('sent_to_llm', 0)}")
    print(f"  skipped (merge):        {stats.get('skipped_by_merge', 0)}")
    print(f"  skipped (docs):         {stats.get('skipped_by_docs', 0)}")
    print(f"  skipped (bugfix):       {stats.get('skipped_by_bugfix', 0)}")
    print(f"  skipped (small):        {stats.get('skipped_by_size', 0)}")
    print(f"  candidates for CHANGELOG: {stats.get('candidates_for_changelog', 0)}")
    print(f"  external contributors:  {stats.get('external_contributors', 0)}")
    print()

    # Stage 5.2 (T9) — not yet implemented.
    review_report_path = os.path.join(workdir, "05_review_report.md")
    overrides_path = os.path.join(workdir, "05_review_overrides.yaml")
    print("Note: review report and overrides file (stage 5.2) not yet implemented.")
    print()
    print("Next:")
    print("  changelog-tool report")

    return _EXIT_OK
