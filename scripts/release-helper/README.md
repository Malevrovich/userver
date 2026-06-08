# changelog-tool

A helper for preparing a `CHANGELOG` for a new userver release from git commit
history. The tool assists the maintainer — it collects commits, identifies
external contributors, filters out uninteresting commits, uses an LLM to
classify the rest, and produces a reviewable report with a proposed
`CHANGELOG`. It does **not** fully automate the release process.

See the full specification in
[`vibe/specification.md`](vibe/specification.md).

## Workflow

```bash
changelog-tool collect   # stages 1-4: collect, contributors, heuristics, LLM
changelog-tool review    # stage 5: verify and prepare review data
changelog-tool report    # stages 6-7: changelog items and final report
```

Range overrides are supported, for example:

```bash
changelog-tool collect --from v2.5.0 --to HEAD
```

The primary configuration mechanism is the `changelog.yaml` file.

## Running

Without installation, from the repository root:

```bash
./scripts/release-helper/changelog-tool --help
```

or as a module:

```bash
cd scripts/release-helper && python -m changelog_tool --help
```

## Status

This is an in-progress implementation. The current state is the CLI skeleton
(task T0); pipeline stages are added by subsequent tasks. Full documentation
and an example `changelog.yaml` are provided in task T13.
