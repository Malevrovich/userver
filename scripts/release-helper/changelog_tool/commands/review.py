"""``review`` command — pipeline stage 5.

Stage 5:
    5.1. Verify that no commit was lost (machine checks).
    5.2. Prepare human-readable review data and the overrides file.

Intended outputs (created by later tasks):
    .changelog/05_verified_classification.jsonl
    .changelog/05_verification_report.json
    .changelog/05_review_report.md
    .changelog/05_review_overrides.yaml

T0 provides only a stub that describes the command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from changelog_tool.commands import print_config_summary

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext
    from changelog_tool.config import Config

_INTENDED_OUTPUTS = (
    "05_verified_classification.jsonl",
    "05_verification_report.json",
    "05_review_report.md",
    "05_review_overrides.yaml",
)


def run_review(ctx: "CliContext") -> int:
    """Run the ``review`` command.

    Loads the resolved configuration and dispatches to :func:`_review`.
    Pipeline logic for stage 5 lands in later tasks.
    """

    from changelog_tool.cli import run_with_config

    return run_with_config(ctx, _review)


def _review(ctx: "CliContext", config: "Config") -> int:
    print("changelog-tool review")
    print("Stage: 5 (verification + review data preparation)")
    print_config_summary(config)
    print("Intended outputs:")
    for name in _INTENDED_OUTPUTS:
        print(f"  {config.output.workdir}/{name}")
    print("Not yet implemented (pipeline stages added in later tasks).")
    return 0
