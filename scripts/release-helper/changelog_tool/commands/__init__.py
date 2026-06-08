"""Command handlers for the changelog-tool CLI.

Each module exposes a ``run_<command>(ctx)`` function that receives a
:class:`changelog_tool.cli.CliContext` and returns a process exit code. The
handlers load the resolved configuration via
:func:`changelog_tool.cli.run_with_config`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from changelog_tool.config import Config


def print_config_summary(config: "Config") -> None:
    """Print a short human-readable summary of the resolved configuration."""

    print("Configuration:")
    print(f"  config file: {config.source_path}")
    print(f"  repo:        {config.repo.owner}/{config.repo.name}")
    print(f"  github_url:  {config.repo.github_url}")
    print(f"  local_path:  {config.repo.local_path}")
    print(f"  range:       {config.range.from_ref}..{config.range.to_ref}")
    print(
        "  thresholds:  "
        f"small_commit={config.thresholds.small_commit}, "
        f"bugfix_skip={config.thresholds.bugfix_skip}"
    )
    print(
        "  llm:         "
        f"batch_size={config.llm.batch_size}, "
        f"max_prompt_chars={config.llm.max_prompt_chars}"
    )
    print(f"  core_team:   {len(config.core_team.logins)} login(s)")
    print(f"  workdir:     {config.output.workdir}")
