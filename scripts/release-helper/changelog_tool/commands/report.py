"""``report`` command — pipeline stages 6 and 7.

Stages:
    6. Generate CHANGELOG items (with resumable missing-line generation).
    7. Generate the final human-readable report.

Intended outputs (created by later tasks):
    .changelog/06_changelog_items.jsonl
    .changelog/06_changelog_line_generation_state.json
    .changelog/07_final_report.md

T0 provides only a stub that describes the command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from changelog_tool.commands import print_config_summary

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext
    from changelog_tool.config import Config

_INTENDED_OUTPUTS = (
    "06_changelog_items.jsonl",
    "06_changelog_line_generation_state.json",
    "07_final_report.md",
)


def run_report(ctx: "CliContext") -> int:
    """Run the ``report`` command.

    Loads the resolved configuration and dispatches to :func:`_report`.
    Pipeline logic for stages 6-7 lands in later tasks.
    """

    from changelog_tool.cli import run_with_config

    return run_with_config(ctx, _report)


def _report(ctx: "CliContext", config: "Config") -> int:
    print("changelog-tool report")
    print("Stages: 6 (changelog items), 7 (final report)")
    print_config_summary(config)
    print("Intended outputs:")
    for name in _INTENDED_OUTPUTS:
        print(f"  {config.output.workdir}/{name}")
    print("Not yet implemented (pipeline stages added in later tasks).")
    return 0
