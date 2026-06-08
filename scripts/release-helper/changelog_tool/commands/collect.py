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

from changelog_tool.commands import print_config_summary

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext
    from changelog_tool.config import Config

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
    """Run the ``collect`` command.

    Loads the resolved configuration and dispatches to :func:`_collect`.
    Pipeline logic for stages 1-4 lands in later tasks.
    """

    from changelog_tool.cli import run_with_config

    return run_with_config(ctx, _collect)


def _collect(ctx: "CliContext", config: "Config") -> int:
    print("changelog-tool collect")
    print("Stages: 1 (collect), 2 (contributors), 3 (pre-classify), 4 (LLM)")
    print_config_summary(config)
    print("Intended outputs:")
    for name in _INTENDED_OUTPUTS:
        print(f"  {config.output.workdir}/{name}")
    print("Not yet implemented (pipeline stages added in later tasks).")
    return 0
