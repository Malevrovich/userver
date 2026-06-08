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

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext

_INTENDED_OUTPUTS = (
    "06_changelog_items.jsonl",
    "06_changelog_line_generation_state.json",
    "07_final_report.md",
)


def run_report(ctx: "CliContext") -> int:
    """Run the ``report`` command (stub for T0)."""

    print("changelog-tool report")
    print("Stages: 6 (changelog items), 7 (final report)")
    print(f"Config: {ctx.config_path}")
    print("Intended outputs:")
    for name in _INTENDED_OUTPUTS:
        print(f"  .changelog/{name}")
    print("Not yet implemented (T0 skeleton).")
    return 0
