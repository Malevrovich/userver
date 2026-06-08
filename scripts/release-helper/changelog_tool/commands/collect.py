"""``collect`` command — pipeline stages 1, 2, 3, 4.

Stages:
    1. Collect commits from git history.
    2. Identify external contributors.
    3. Automatic pre-classification via heuristics.
    4. LLM analysis of remaining commits (resumable).

Intended outputs (created by later tasks):
    .changelog/01_commits.jsonl
    .changelog/01_commits_manifest.json
    .changelog/02_commits_with_contributors.jsonl
    .changelog/02_external_contributors.md
    .changelog/03_preclassified.jsonl
    .changelog/04_llm_classified.jsonl
    .changelog/04_llm_classification_state.json

T0 provides only a stub that describes the command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext

_INTENDED_OUTPUTS = (
    "01_commits.jsonl",
    "01_commits_manifest.json",
    "02_commits_with_contributors.jsonl",
    "02_external_contributors.md",
    "03_preclassified.jsonl",
    "04_llm_classified.jsonl",
    "04_llm_classification_state.json",
)


def run_collect(ctx: "CliContext") -> int:
    """Run the ``collect`` command (stub for T0)."""

    print("changelog-tool collect")
    print("Stages: 1 (collect), 2 (contributors), 3 (pre-classify), 4 (LLM)")
    print(f"Config: {ctx.config_path}")
    if ctx.from_ref or ctx.to_ref:
        print(f"Range override: {ctx.from_ref or '<config>'}..{ctx.to_ref or '<config>'}")
    print("Intended outputs:")
    for name in _INTENDED_OUTPUTS:
        print(f"  .changelog/{name}")
    print("Not yet implemented (T0 skeleton).")
    return 0
