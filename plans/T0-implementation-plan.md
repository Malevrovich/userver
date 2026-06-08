# T0 Implementation Plan — Project scaffold and CLI skeleton

Parent breakdown: [`plans/changelog-tool-task-breakdown.md`](plans/changelog-tool-task-breakdown.md:1)
Spec: [`scripts/release-helper/vibe/specification.md`](scripts/release-helper/vibe/specification.md:1) (sections 2.5, 3, 13)

## Goal of T0

Stand up the Python package skeleton and a working `changelog-tool` CLI that exposes the three commands (`collect`, `review`, `report`), parses global options, loads nothing real yet, and dispatches to per-command handler stubs. No pipeline logic — just a runnable, well-structured entry point that later tasks fill in.

## Conventions (from existing `scripts/` tooling)

- `from __future__ import annotations` at top of modules.
- Standard library `argparse` for CLI (consistent with [`scripts/sql/generator.py`](scripts/sql/generator.py:3)).
- 4-space indentation, type hints, dataclasses where useful.
- Per-tool `requirements.txt` (like [`scripts/sql/requirements.txt`](scripts/sql/requirements.txt:1)).

## Target directory layout

```text
scripts/release-helper/
├── changelog_tool/
│   ├── __init__.py            # package version constant
│   ├── __main__.py            # enables `python -m changelog_tool`
│   ├── cli.py                 # argparse parser + dispatch
│   └── commands/
│       ├── __init__.py
│       ├── collect.py         # stub: run_collect(ctx)
│       ├── review.py          # stub: run_review(ctx)
│       └── report.py          # stub: run_report(ctx)
├── changelog-tool             # thin executable entry script (shebang)
├── requirements.txt
└── README.md                  # placeholder, filled in T13
```

## Implementation steps

1. **Package init** — create `changelog_tool/__init__.py` with `__version__` and package docstring.

2. **CLI context object** — define a small dataclass (e.g. `CliContext`) in `cli.py` holding parsed global options:
   - `config_path: str` (default `changelog.yaml`)
   - `from_ref: str | None`
   - `to_ref: str | None`
   - `workdir: str | None` (override of `output.workdir`)
   - `verbose: bool`
   These are passed to handlers; real config loading lands in T1.

3. **Argument parser** (`cli.py::build_parser`):
   - Program name `changelog-tool`.
   - Global args: `--config`, `--from`, `--to`, `--workdir`, `-v/--verbose`, `--version`.
   - Subparsers: `collect`, `review`, `report`, each with `set_defaults(func=...)`.
   - `collect` additionally accepts `--from`/`--to` overrides (Spec 13.1) — implemented as shared parent parser so the options are available both globally and per-command.

4. **Dispatch** (`cli.py::main`):
   - Parse args; build `CliContext`.
   - Call the selected `func(ctx)`; if no subcommand given, print help and exit non-zero.
   - Wrap dispatch in a top-level try/except that maps known errors to clear messages + non-zero exit codes (placeholder exit-code map aligned with Spec 14; full error taxonomy in T12).

5. **Command stubs** (`commands/*.py`):
   - Each exposes `run_<cmd>(ctx: CliContext) -> int`.
   - For now: print the stage mapping and intended outputs, then a `NotImplementedError`-style "not yet implemented" notice returning a non-zero/zero code as decided below.
   - Document stage mapping in docstrings: collect→1-4, review→5, report→6-7 (Spec 3).

6. **Entry points**:
   - `changelog_tool/__main__.py` calls `cli.main()`.
   - `scripts/release-helper/changelog-tool` executable shell/py shim invoking the module, made `chmod +x`.

7. **requirements.txt** — list runtime deps anticipated by later tasks but needed for the skeleton now: `PyYAML` (config), nothing else mandatory for T0. Keep minimal; add as tasks need them.

8. **README placeholder** — one-paragraph description + the three-command workflow snippet (full docs in T13).

## Open decisions (please confirm)

1. **Package location/name**: `scripts/release-helper/changelog_tool/` with CLI name `changelog-tool`. OK?
2. **Stub behavior**: should stubs exit `0` with a "not implemented" message, or exit non-zero? I propose exit `0` + clear notice so the skeleton is demoable.
3. **Python version floor**: target `3.8+` (uses `from __future__ import annotations` so `X | Y` hints are fine in annotations). OK, or pin higher (e.g. 3.10+)?
4. **No new third-party CLI framework** (argparse only, matching repo). Confirm you don't want `click`.

## Out of scope for T0 (handled later)

- Real `changelog.yaml` parsing/validation → T1.
- Any pipeline/stage logic, file artifacts → T3+.
- Logging directory, caching, full error taxonomy → T12.
- Full README/example config → T13.

## Done criteria for T0

- `python -m changelog_tool --help` and `./changelog-tool --help` show all three commands and global options.
- `changelog-tool collect|review|report` each run, print their stage mapping/intended outputs, and exit cleanly.
- `--version` prints the package version.
- Package importable; layout matches the tree above.
