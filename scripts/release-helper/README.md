# CHANGELOG Preparation Tool

This tool assists maintainers in preparing a `CHANGELOG` for a new release based on the git commit history. It uses heuristics and an LLM to classify commits, identify external contributors, and generate human-readable changelog lines.

**Important:** The tool does not fully automate the release process. It prepares data for the maintainer to review, correct, and approve.

## Configuration

The tool is configured via `changelog.yaml` in the current directory. See the provided `changelog.yaml` for an example.

You can override the config path using the `--config` flag:
```bash
./changelog-tool --config path/to/changelog.yaml collect
```

### LLM Configuration
The tool supports different LLM backends (currently `http_api` and `fake`). For the `http_api` backend, you need to provide an API key, base URL, and model name.

**It is highly recommended to set these via environment variables** so you don't have to pass them or type them every time:
```bash
export CHANGELOG_LLM_API_KEY="your-api-key"
export CHANGELOG_LLM_BASE_URL="https://api.openai.com/v1" # Raw interface
export CHANGELOG_LLM_MODEL="gpt-4o-mini"
```

Alternatively, you can provide them via:
1. CLI flags: `--llm-api-key`, `--llm-base-url`, `--llm-model`
2. Interactive prompts (if running in a terminal and the values are not provided).

## Workflow

The normal workflow consists of three commands:

### 1. Collect
```bash
./changelog-tool collect
```
*(Or with overrides: `./changelog-tool collect --from v2.5.0 --to HEAD`)*

This command:
1. Collects all commits in the specified range from the local git repository.
2. Identifies external contributors via GitHub.
3. Applies heuristic pre-classification (filters out merge commits, docs, small bugfixes, and small commits).
4. Sends the remaining commits to the LLM for classification.

**Resumability:** LLM classification is fully resumable. If the process is interrupted (e.g., network error, rate limit), simply run `collect` again. It will continue from where it left off without losing already processed batches.

### 2. Review
```bash
./changelog-tool review
```

This command:
1. Verifies that no commits were lost during the collection and classification stages.
2. Generates a human-readable Markdown report (`.changelog/05_review_report.md`) sorted by commit size.
3. Generates a YAML overrides template (`.changelog/05_review_overrides.yaml`).

**Maintainer Action:**
Open `.changelog/05_review_report.md` to review the classifications. If any corrections are needed (e.g., changing a category, including/excluding a commit, or rewriting a changelog line), edit `.changelog/05_review_overrides.yaml`.

### 3. Report
```bash
./changelog-tool report
```

This command:
1. Applies your manual overrides from `.changelog/05_review_overrides.yaml`.
2. Uses the LLM to generate missing changelog lines for any commits selected for inclusion that don't have one yet. (This step is also fully resumable).
3. Generates the final report (`.changelog/07_final_report.md`) containing the proposed CHANGELOG, external contributors, statistics, and any items requiring human attention.

## Artifact Layout

The tool generates artifacts in the `.changelog/` directory (configurable via `output.workdir`). These files represent the state of the pipeline at each stage:

```text
.changelog/
├── 01_commits.jsonl                        # Full list of commits from git
├── 01_commits_manifest.json                # SHA manifest and checksum
│
├── 02_commits_with_contributors.jsonl      # Commits with GitHub authors/co-authors
├── 02_external_contributors.md             # List of external contributors
│
├── 03_preclassified.jsonl                  # Result of heuristics
│
├── 04_llm_classified.jsonl                 # Result of LLM classification
├── 04_llm_classification_state.json        # State of resumable LLM classification
│
├── 05_verified_classification.jsonl        # Unified classification after merge
├── 05_verification_report.json             # Report on invariant checks
├── 05_review_report.md                     # Report for the maintainer
├── 05_review_overrides.yaml                # Manual maintainer overrides
│
├── 06_changelog_items.jsonl                # Final proposed CHANGELOG items
├── 06_changelog_line_generation_state.json # State of missing line generation
│
├── 07_final_report.md                      # Final human-readable report
│
└── logs/                                   # Execution logs
```
