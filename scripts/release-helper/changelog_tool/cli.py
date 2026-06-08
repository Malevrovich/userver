"""Command-line interface for the CHANGELOG preparation tool.

This module defines the ``changelog-tool`` CLI with three subcommands that map
to the pipeline stages described in the specification:

    collect -> stages 1, 2, 3, 4
    review  -> stage 5
    report  -> stages 6, 7

T0 only wires up argument parsing and dispatch to command handler stubs. Real
configuration loading lands in T1; pipeline logic lands in later tasks.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from typing import Callable, List, Optional, Sequence

from changelog_tool import __version__
from changelog_tool.config import Config, ConfigError, apply_overrides, load_config

DEFAULT_CONFIG_PATH = "changelog.yaml"

# Exit codes. The full error taxonomy is defined in T12; for now we keep a small
# set of stable codes so the skeleton behaves predictably.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3


@dataclasses.dataclass
class CliContext:
    """Parsed global options shared by all commands.

    Real configuration loading and merging of these overrides with
    ``changelog.yaml`` happens in T1. For now these are passed verbatim to the
    command handlers.
    """

    command: str
    config_path: str = DEFAULT_CONFIG_PATH
    from_ref: Optional[str] = None
    to_ref: Optional[str] = None
    workdir: Optional[str] = None
    verbose: bool = False


# A command handler takes a CliContext and returns a process exit code.
CommandHandler = Callable[[CliContext], int]


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add options shared between the top-level parser and subcommands.

    ``--from`` / ``--to`` are accepted both globally and on ``collect`` so that
    overrides work in the form documented in the specification (section 13.1):

        changelog-tool collect --from v2.5.0 --to HEAD
    """

    parser.add_argument(
        "--config",
        dest="config_path",
        metavar="PATH",
        help=f"path to the configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--from",
        dest="from_ref",
        metavar="REF",
        help="override range.from (previous release tag or SHA)",
    )
    parser.add_argument(
        "--to",
        dest="to_ref",
        metavar="REF",
        help="override range.to (current tag, SHA, branch, or HEAD)",
    )
    parser.add_argument(
        "--workdir",
        dest="workdir",
        metavar="DIR",
        help="override output.workdir (directory for pipeline artifacts)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        help="enable verbose output",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with the three subcommands."""

    parser = argparse.ArgumentParser(
        prog="changelog-tool",
        description=(
            "Prepare a CHANGELOG for a new release from git commit history. "
            "The tool assists the maintainer; it does not fully automate the "
            "release process."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    _add_common_arguments(parser)

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # Import here to avoid a circular import at module load time.
    from changelog_tool.commands import collect, report, review

    collect_parser = subparsers.add_parser(
        "collect",
        help="collect commits, contributors, pre-classify, and run LLM analysis",
        description="Runs pipeline stages 1, 2, 3, and 4.",
    )
    _add_common_arguments(collect_parser)
    collect_parser.set_defaults(func=collect.run_collect)

    review_parser = subparsers.add_parser(
        "review",
        help="verify the pipeline and prepare review data for the maintainer",
        description="Runs pipeline stage 5.",
    )
    _add_common_arguments(review_parser)
    review_parser.set_defaults(func=review.run_review)

    report_parser = subparsers.add_parser(
        "report",
        help="generate CHANGELOG items and the final report",
        description="Runs pipeline stages 6 and 7.",
    )
    _add_common_arguments(report_parser)
    report_parser.set_defaults(func=report.run_report)

    return parser


def _build_context(args: argparse.Namespace) -> CliContext:
    """Build a CliContext from parsed arguments.

    Options may appear before or after the subcommand (both the top-level
    parser and each subparser register them). The subcommand value, when
    provided, takes precedence over the top-level value.
    """

    def pick(name: str, default: object = None) -> object:
        # argparse stores the most recently parsed value under the same dest,
        # so a single getattr already reflects subcommand precedence. This
        # helper keeps the intent explicit and centralizes defaulting.
        return getattr(args, name, default)

    return CliContext(
        command=args.command,
        config_path=pick("config_path") or DEFAULT_CONFIG_PATH,
        from_ref=pick("from_ref"),
        to_ref=pick("to_ref"),
        workdir=pick("workdir"),
        verbose=bool(pick("verbose", False)),
    )


def load_resolved_config(ctx: CliContext) -> Config:
    """Load the config for ``ctx`` and apply CLI overrides.

    Raises :class:`changelog_tool.config.ConfigError` on any problem; command
    handlers are expected to translate that into a clean error + exit code via
    :func:`run_with_config`.
    """

    config = load_config(ctx.config_path)
    apply_overrides(
        config,
        from_ref=ctx.from_ref,
        to_ref=ctx.to_ref,
        workdir=ctx.workdir,
    )
    return config


def run_with_config(ctx: CliContext, runner: "Callable[[CliContext, Config], int]") -> int:
    """Load the resolved config and invoke ``runner``, handling config errors.

    On :class:`ConfigError`, prints ``ERROR: <message>`` to stderr and returns
    :data:`EXIT_CONFIG`. Configuration warnings are printed to stderr.
    """

    try:
        config = load_resolved_config(ctx)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    for warning in config.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    return runner(ctx, config)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Returns a process exit code."""

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    handler: Optional[CommandHandler] = getattr(args, "func", None)
    if args.command is None or handler is None:
        parser.print_help()
        return EXIT_USAGE

    ctx = _build_context(args)
    return handler(ctx)


def run() -> None:
    """Console-script style wrapper that exits the process."""

    sys.exit(main())


__all__: List[str] = [
    "CliContext",
    "build_parser",
    "main",
    "run",
    "load_resolved_config",
    "run_with_config",
    "DEFAULT_CONFIG_PATH",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_CONFIG",
]
