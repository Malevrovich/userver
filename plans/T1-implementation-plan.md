# T1 Implementation Plan — Configuration loader (`changelog.yaml`)

Parent breakdown: [`plans/changelog-tool-task-breakdown.md`](plans/changelog-tool-task-breakdown.md:1)
Spec: [`scripts/release-helper/vibe/specification.md`](scripts/release-helper/vibe/specification.md:135) (section 4)

## Goal

Load, validate, and normalize `changelog.yaml` into typed config objects, apply
CLI overrides from `CliContext` (`--from`, `--to`, `--workdir`), and make the
result available to all command handlers. Replace the stub printing in the
three commands with real config loading + a clear error path for bad configs.

## Config schema (Spec §4.1)

```yaml
repo:
  owner: userver-framework
  name: userver
  github_url: https://github.com/userver-framework/userver
  local_path: .
range:
  from: v2.5.0
  to: HEAD
thresholds:
  small_commit: 50
  bugfix_skip: 200
llm:
  batch_size: 20
  max_prompt_chars: 50000
core_team:
  logins: [antoshkka, AlekSi, some-maintainer]
output:
  workdir: .changelog
```

## Files to create

```text
scripts/release-helper/changelog_tool/
├── config.py        # dataclasses + load_config() + ConfigError
└── (commands/*.py updated to load and echo real config)
```

## Design

1. **Dataclasses** in `config.py`:
   - `RepoConfig(owner, name, github_url, local_path)`
   - `RangeConfig(from_ref, to_ref)` — note `from` is a Python keyword, store as `from_ref`/`to_ref`.
   - `ThresholdsConfig(small_commit, bugfix_skip)`
   - `LlmConfig(batch_size, max_prompt_chars)`
   - `CoreTeamConfig(logins: list[str])`
   - `OutputConfig(workdir)`
   - `Config` aggregating all of the above.

2. **Defaults** (applied when keys are absent):
   - `thresholds.small_commit = 50`, `thresholds.bugfix_skip = 200`
   - `llm.batch_size = 20`, `llm.max_prompt_chars = 50000`
   - `output.workdir = ".changelog"`
   - `repo.local_path = "."`, `range.to = "HEAD"`
   - `core_team.logins = []`

3. **Required fields** (error if missing/empty):
   - `repo.owner`, `repo.name`, `repo.github_url`
   - `range.from`
   (`range.to` defaults to `HEAD`.)

4. **`ConfigError`** exception with a clear message (file path + reason). Used
   for: file not found, invalid YAML, wrong types, missing required fields,
   unknown top-level keys (warn rather than fail — keep forward-compatible;
   decision: warn-only to avoid breaking on future keys).

5. **`load_config(path) -> Config`**:
   - Read YAML via `yaml.safe_load`.
   - Validate top-level structure is a mapping.
   - Build each section with type checks and defaults.
   - Validate threshold/llm values are positive ints.

6. **CLI override application** — `apply_overrides(config, ctx) -> Config`:
   - `--from` → `range.from_ref`
   - `--to` → `range.to_ref`
   - `--workdir` → `output.workdir`
   - Returns a new/mutated config; non-None overrides win.

7. **Helper** `Config.commit_url(sha)` building
   `{github_url}/commit/{sha}` (used later by T3) — small convenience now,
   keeps URL construction in one place.

8. **Command integration**: each `run_*` loads config via a shared helper
   (e.g. `cli._load_config(ctx)` or directly), applies overrides, and prints a
   short resolved summary (range, workdir, core-team size) instead of the
   hardcoded `changelog.yaml` echo. On `ConfigError`, print `ERROR: ...` to
   stderr and return a non-zero exit code.

## Error behavior

- Missing config file → `ConfigError` → `ERROR: config file not found: <path>` , exit non-zero.
- Invalid YAML → `ConfigError` with YAML parser message.
- Missing required field → `ConfigError` naming the field.
- Wrong type → `ConfigError` naming the field and expected type.

(Exit-code taxonomy stays minimal here; full taxonomy is T12. Use a non-zero
config-error code, e.g. `EXIT_CONFIG = 3`.)

## Out of scope for T1

- Reading/validating that `repo.local_path` is a real git repo → T3.
- LLM provider connection details (not fixed by spec).
- Any artifact writing.

## Done criteria

- `load_config` parses the spec's minimal example into a `Config` with correct defaults.
- Missing/invalid configs produce clear `ConfigError` messages and non-zero exit.
- `--from/--to/--workdir` overrides are reflected in the resolved config.
- `collect/review/report` print a resolved config summary and exit 0 with a valid config.
- A sample `changelog.yaml` is used to manually verify (created temporarily; the committed example lands in T13).
