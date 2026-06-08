# Specification: Tool for Preparing a CHANGELOG from Git History

## 1. Context and Goal

We need a tool that helps a maintainer prepare a `CHANGELOG` for a new release based on the git commit history since the previous release.

The tool must:

1. Get all commits between two git refs.
2. Not lose any commit.
3. Find all external contributors and prepare their list for mandatory mention.
4. Automatically filter out commits that are obviously uninteresting for the `CHANGELOG`:
   - small commits;
   - documentation commits;
   - small bugfix commits;
   - merge commits.
5. Send the remaining potentially important commits to an LLM for classification.
6. Prepare data for maintainer review.
7. After manual corrections, generate a final report with the proposed `CHANGELOG`.

The tool **must not fully automate the release process**.  
It only helps the maintainer collect, classify, and verify the data.

---

## 2. Main Principles

### 2.1. Git history is the source of truth

The full list of commits is taken from git.

All subsequent stages must preserve the set of SHA values from the original list.

If at any stage a commit is lost, duplicated, or an extra SHA appears, the pipeline must terminate with an error.

---

### 2.2. LLM is an assistant, not the source of truth

The LLM is used only to analyze non-trivial commits and generate missing `CHANGELOG` lines.

The LLM must not:

- determine the full list of commits;
- determine the list of contributors;
- change SHA values;
- delete commits;
- make the final decision instead of the maintainer.

---

### 2.3. All LLM operations must be resumable

Any operation that calls an LLM must save intermediate state to disk.

If execution is interrupted due to:

- a network error;
- an LLM provider error;
- rate limiting;
- process crash;
- manual interruption;

a repeated run of the corresponding command must be able to continue from where it stopped without losing already obtained results.

This requirement applies at minimum to:

1. LLM classification of commits at stage 4.
2. LLM generation of missing `CHANGELOG` lines at stage 6.

---

### 2.4. External contributors must not be lost

External contributors must appear in the final report even if their commit:

- is small;
- is documentation-only;
- is a typo fix;
- is a co-author contribution;
- did not make it into the proposed `CHANGELOG`.

---

### 2.5. Simple interface

The normal workflow must consist of three commands:

```bash
changelog-tool collect
changelog-tool review
changelog-tool report
```

Overrides via flags are allowed, for example:

```bash
changelog-tool collect --from v2.5.0 --to HEAD
```

But the primary configuration mechanism is the `changelog.yaml` file.

---

## 3. High-Level Workflow

The pipeline consists of seven logical stages.

```text
1. Collect commits
2. Identify external contributors
3. Automatic pre-classification
   3.1. Merge commits
   3.2. Docs by subject
   3.3. Fix/bug by subject and size
   3.4. Small commits
4. LLM analysis of remaining commits
5. Verification and preparation of review data
   5.1. Verify that nothing was lost
   5.2. Prepare human-readable review report
6. Generate CHANGELOG items
7. Generate final report
```

CLI commands map to stages as follows:

```text
collect -> stages 1, 2, 3, 4
review  -> stage 5
report  -> stages 6, 7
```

---

## 4. Configuration

The tool must read configuration from the file:

```text
changelog.yaml
```

Minimal example:

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
  logins:
    - antoshkka
    - AlekSi
    - some-maintainer

output:
  workdir: .changelog
```

---

### 4.1. Configuration fields

#### `repo`

```yaml
repo:
  owner: userver-framework
  name: userver
  github_url: https://github.com/userver-framework/userver
  local_path: .
```

- `owner` — GitHub owner or organization.
- `name` — repository name.
- `github_url` — base URL of the repository.
- `local_path` — path to the local git clone.

---

#### `range`

```yaml
range:
  from: v2.5.0
  to: HEAD
```

- `from` — previous release tag or SHA.
- `to` — current tag, SHA, branch, or `HEAD`.

The commit range is interpreted as:

```bash
git log <from>..<to>
```

---

#### `thresholds`

```yaml
thresholds:
  small_commit: 50
  bugfix_skip: 200
```

- `small_commit` — commits with `size_score <= small_commit` are considered small and are not sent to the LLM.
- `bugfix_skip` — fix/bug commits with `size_score <= bugfix_skip` are not sent to the LLM.

---

#### `llm`

```yaml
llm:
  batch_size: 20
  max_prompt_chars: 50000
```

- `batch_size` — maximum number of commits in one LLM batch.
- `max_prompt_chars` — maximum number of characters in one prompt.

Connection details for a concrete LLM provider are not fixed by this specification.

---

#### `core_team`

```yaml
core_team:
  logins:
    - antoshkka
    - AlekSi
    - some-maintainer
```

List of GitHub logins of the core team.

Used to determine external contributors.

If the `github_login` of an author or co-author is not present in this list, they are considered an external contributor.

If `github_login == UNKNOWN`, the author or co-author is also considered an external contributor and marked as requiring attention.

---

#### `output`

```yaml
output:
  workdir: .changelog
```

Directory for pipeline artifacts.

---

## 5. Main Data Entities

### 5.1. Commit

Basic representation of a commit.

```json
{
  "sha": "991c62abad9ad065cb0af7388616ed340c5ff57a",
  "short_sha": "991c62ab",
  "is_merge_commit": false,

  "author_name": "Ivan Petrov",
  "author_email": "ivan@example.com",
  "github_login": "ivan-petrov",
  "github_profile_url": "https://github.com/ivan-petrov",
  "is_external": true,

  "co_authors": [
    {
      "name": "Petr Sidorov",
      "email": "petr@example.com",
      "github_login": "petr-sidorov",
      "github_profile_url": "https://github.com/petr-sidorov",
      "is_external": true
    }
  ],

  "subject": "Add support for Redis pipeline",
  "body": "Long commit message...",
  "message": "Add support for Redis pipeline\n\nLong commit message...",
  "commit_url": "https://github.com/userver-framework/userver/commit/991c62abad9ad065cb0af7388616ed340c5ff57a",

  "changed_files": [
    "redis/include/userver/redis/pipeline.hpp",
    "redis/src/pipeline.cpp",
    "redis/tests/pipeline_test.cpp",
    "docs/redis.md"
  ],

  "insertions": 240,
  "deletions": 31,
  "files_count": 4,
  "size_score": 271,
  "size_bucket": "large"
}
```

At stage 1, the fields `github_login`, `github_profile_url`, `is_external`, and GitHub-related data in `co_authors` may be missing or `null`.

They are filled at stage 2.

---

### 5.2. Size score

Commit size is calculated as:

```text
size_score = insertions + deletions
```

---

### 5.3. Size bucket

Recommended values:

```text
tiny   <= 10
small  <= 50
medium <= 200
large  <= 1000
huge   > 1000
```

---

### 5.4. Merge commits

Merge commits are not excluded from the source list.

They participate in the checksum and must be preserved in all final artifacts.

For merge commits, the following is set:

```json
{
  "is_merge_commit": true
}
```

Merge commits are not considered ordinary meaningful commits and must be marked as `unclear` at the pre-classification stage.

---

### 5.5. Co-authors

The tool must parse trailers of the following form:

```text
Co-authored-by: Name <email>
```

from the commit body.

Each co-author must be added to the array:

```json
{
  "co_authors": [
    {
      "name": "Name",
      "email": "email@example.com"
    }
  ]
}
```

At stage 2, co-authors must be enriched with GitHub logins and must participate in building the list of external contributors on equal terms with the primary author.

---

## 6. Stage 1: Collect Commits

### 6.1. Goal

Collect the full list of commits between `range.from` and `range.to`.

---

### 6.2. Input

- `changelog.yaml`
- local git repository

---

### 6.3. Output

```text
.changelog/01_commits.jsonl
.changelog/01_commits_manifest.json
```

---

### 6.4. `01_commits.jsonl`

Format: JSONL, one line per `Commit` object.

Each commit must contain:

- full SHA;
- short SHA;
- `is_merge_commit` flag;
- author name;
- author email;
- list of co-authors extracted from `Co-authored-by`;
- subject;
- body;
- full message;
- commit URL;
- changed files;
- insertions;
- deletions;
- files count;
- size score;
- size bucket.

---

### 6.5. Merge commits

The tool must not use `--no-merges` by default.

Merge commits must be included in `01_commits.jsonl`.

The `is_merge_commit` flag must be set based on git metadata, for example by the number of parents:

```text
is_merge_commit = parents_count > 1
```

Merge commits participate in the checksum on equal terms with regular commits.

---

### 6.6. `01_commits_manifest.json`

Example:

```json
{
  "from_ref": "v2.5.0",
  "to_ref": "HEAD",
  "total_commits": 147,
  "sha_list": [
    "991c62abad9ad065cb0af7388616ed340c5ff57a"
  ],
  "sha_checksum": "sha256-of-sorted-sha-list",
  "generated_at": "2026-06-08T12:00:00Z"
}
```

---

### 6.7. SHA checksum

Formula:

```text
sha_checksum = sha256("\n".join(sorted(full_sha_list)))
```

---

### 6.8. Invariants

- All SHA values are unique.
- `total_commits == len(sha_list)`.
- `sha_checksum` is calculated from full SHA values.
- SHA order must not affect the checksum.

---

## 7. Stage 2: Identify External Contributors

### 7.1. Goal

Determine the GitHub login of the author and co-authors of each commit and decide who is an external contributor.

---

### 7.2. Input

```text
.changelog/01_commits.jsonl
changelog.yaml
```

The core-team list is taken from:

```yaml
core_team:
  logins:
    - ...
```

---

### 7.3. Output

```text
.changelog/02_commits_with_contributors.jsonl
.changelog/02_external_contributors.md
```

---

### 7.4. Enriched commit

The following fields are added to or filled in the commit object:

```json
{
  "github_login": "ivan-petrov",
  "github_profile_url": "https://github.com/ivan-petrov",
  "is_external": true,
  "co_authors": [
    {
      "name": "Petr Sidorov",
      "email": "petr@example.com",
      "github_login": "petr-sidorov",
      "github_profile_url": "https://github.com/petr-sidorov",
      "is_external": true
    }
  ]
}
```

---

### 7.5. How to obtain GitHub login

The implementation may use:

1. GitHub API by commit SHA.
2. Local email → GitHub login mapping, if added by the implementation.
3. Another mechanism, if it provides a correct GitHub login.

If the login could not be determined:

```json
{
  "github_login": "UNKNOWN",
  "github_profile_url": null,
  "is_external": true
}
```

`UNKNOWN` is considered external / requiring attention so that such an author is not lost.

---

### 7.6. External contributor rule

External contributors are determined from primary authors and co-authors.

For each participant of a commit:

```text
is_external = github_login NOT IN core_team.logins
```

If:

```text
github_login == UNKNOWN
```

then:

```text
is_external = true
```

Such a participant must receive an attention flag.

If the primary author is core-team, but at least one co-author is external, the external co-author must still be included in the list of external contributors.

---

### 7.7. `02_external_contributors.md`

The file must contain all external contributors and their commits.

The exact human-readable format will be designed separately, but it must include:

- GitHub login;
- GitHub profile URL, if known;
- `UNKNOWN` marker, if the login is unknown;
- SHA of each commit;
- link to each commit;
- commit subject;
- participant role: `author` or `co-author`.

---

## 8. Stage 3: Automatic Pre-classification

### 8.1. Goal

Filter out commits that do not need to be sent to the LLM.

---

### 8.2. Input

```text
.changelog/02_commits_with_contributors.jsonl
```

---

### 8.3. Output

```text
.changelog/03_preclassified.jsonl
```

---

### 8.4. Field `auto_classification`

Each commit receives the field:

```json
{
  "auto_classification": {
    "send_to_llm": false,
    "reason": "small_commit",
    "category": "small",
    "candidate_for_changelog": false,
    "changelog_line": null,
    "confidence": "high"
  }
}
```

---

### 8.5. Rule order

Rules are applied strictly in this order:

1. Merge commit.
2. Docs by subject.
3. Fix/bug by subject and size.
4. Small size.
5. Everything else is sent to the LLM.

The first matching rule wins.

---

### 8.6. Rule 1: merge commit

If:

```text
is_merge_commit == true
```

then:

```json
{
  "send_to_llm": false,
  "reason": "merge_commit",
  "category": "unclear",
  "candidate_for_changelog": false,
  "changelog_line": null,
  "confidence": "low"
}
```

Merge commits must appear in the review report as requiring attention.

They are not sent to the LLM at stage 4.

---

### 8.7. Rule 2: docs by subject

If `subject` contains:

```text
doc
docs
documentation
readme
changelog
```

then:

```json
{
  "send_to_llm": false,
  "reason": "docs_by_subject",
  "category": "docs",
  "candidate_for_changelog": false,
  "changelog_line": null,
  "confidence": "medium"
}
```

Recommended regex:

```text
\b(doc|docs|documentation|readme|changelog)\b
```

Comparison is case-insensitive.

---

### 8.8. Rule 3: fix/bug by subject and size

If `subject` contains:

```text
fix
bug
bugfix
hotfix
```

and:

```text
size_score <= thresholds.bugfix_skip
```

then:

```json
{
  "send_to_llm": false,
  "reason": "small_or_medium_bugfix",
  "category": "minor_bugfix",
  "candidate_for_changelog": false,
  "changelog_line": null,
  "confidence": "medium"
}
```

Recommended regex:

```text
\b(fix|bug|bugfix|hotfix)\b
```

Comparison is case-insensitive.

The category `minor_bugfix` is used only for bugfix commits filtered out by the heuristic.

It is intentionally different from `important_bugfix`, which may be assigned by the LLM.

`minor_bugfix` is not included in the CHANGELOG by default.

---

### 8.9. Rule 4: small size

If:

```text
size_score <= thresholds.small_commit
```

then:

```json
{
  "send_to_llm": false,
  "reason": "small_commit",
  "category": "small",
  "candidate_for_changelog": false,
  "changelog_line": null,
  "confidence": "high"
}
```

---

### 8.10. Rule 5: send to LLM

If none of the above rules matched:

```json
{
  "send_to_llm": true,
  "reason": "requires_llm_analysis",
  "category": null,
  "candidate_for_changelog": null,
  "changelog_line": null,
  "confidence": null
}
```

---

### 8.11. Invariants

- The output file must contain all SHA values from the input file.
- Each commit must have the `auto_classification` field.
- For each commit, `send_to_llm` must be a boolean.

---

## 9. Stage 4: LLM Analysis of Remaining Commits

### 9.1. Goal

Analyze commits that were not filtered out by heuristics and determine:

- category;
- whether the commit should be included in the `CHANGELOG`;
- a `CHANGELOG` line, if the commit should be included;
- a comment for the maintainer.

---

### 9.2. Input

```text
.changelog/03_preclassified.jsonl
```

Only commits for which the following is true are sent to the LLM:

```json
"auto_classification": {
  "send_to_llm": true
}
```

---

### 9.3. Output

```text
.changelog/04_llm_classified.jsonl
.changelog/04_llm_classification_state.json
```

---

### 9.4. LLM classification result format

```json
{
  "sha": "991c62abad9ad065cb0af7388616ed340c5ff57a",
  "llm_classification": {
    "category": "feature",
    "candidate_for_changelog": true,
    "changelog_line": "Added support for Redis pipelining.",
    "comment": "New user-facing Redis functionality with tests and documentation.",
    "confidence": "high"
  }
}
```

---

### 9.5. LLM categories

Allowed values:

```text
feature
improvement
breaking_change
important_bugfix
internal
docs
tests
chore
unclear
```

---

### 9.6. Rules for inclusion in CHANGELOG

`candidate_for_changelog = true` if the commit adds or changes something noticeable to users:

- new public functionality;
- noticeable improvement to existing functionality;
- breaking change;
- important bugfix affecting users;
- behavior change;
- important build-system change;
- important dependency change;
- newly supported platform;
- newly supported compiler;
- newly supported standard.

`candidate_for_changelog = false` if it is:

- internal refactoring;
- tests;
- CI;
- formatting;
- minor documentation;
- cleanup;
- local change without user-visible effect.

---

### 9.7. Batching

LLM requests must be batched.

Limits:

```text
batch_size <= llm.batch_size
prompt_chars <= llm.max_prompt_chars
```

If a batch exceeds `max_prompt_chars`, it must be split into smaller batches.

A commit is considered an indivisible atom.

A batch may be split only between commits.

If a single commit in its string representation exceeds `llm.max_prompt_chars`, it must not be truncated or partially sent to the LLM.

Instead, such a commit receives fallback classification:

```json
{
  "category": "unclear",
  "candidate_for_changelog": false,
  "changelog_line": null,
  "comment": "Commit is too large to fit into LLM prompt as an atomic unit.",
  "confidence": "low"
}
```

Such a commit must be included in attention flags.

---

### 9.8. Data passed to the LLM

For each commit, pass:

- SHA;
- subject;
- body;
- changed files;
- insertions;
- deletions;
- files count;
- size score;
- commit URL.

The full diff is not passed by default.

---

### 9.9. Resumability of LLM classification

LLM classification must be resumable.

For this purpose, the `collect` command must maintain a state file:

```text
.changelog/04_llm_classification_state.json
```

Example:

```json
{
  "operation": "llm_classification",
  "from_ref": "v2.5.0",
  "to_ref": "HEAD",
  "input_checksum": "sha256-of-input-sha-list",
  "batches": {
    "batch-0001": {
      "status": "done",
      "sha_list": [
        "991c62abad9ad065cb0af7388616ed340c5ff57a"
      ],
      "prompt_hash": "sha256-of-prompt",
      "attempts": 1,
      "updated_at": "2026-06-08T12:00:00Z"
    },
    "batch-0002": {
      "status": "failed",
      "sha_list": [
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
      ],
      "prompt_hash": "sha256-of-prompt",
      "attempts": 2,
      "error": "LLM provider timeout",
      "updated_at": "2026-06-08T12:03:00Z"
    }
  }
}
```

Allowed batch statuses:

```text
pending
in_progress
done
failed
skipped
```

Requirements:

- before sending a batch to the LLM, its status must become `in_progress`;
- after successful classification, the batch status must become `done`;
- results of successful batches must be saved to disk immediately;
- on repeated `collect` runs, batches with status `done` must not be sent again;
- on repeated `collect` runs, batches with status `pending`, `failed`, or stale `in_progress` must be processed;
- `in_progress` left after a process crash must be considered stale and may be restarted;
- `04_llm_classified.jsonl` must be formed from saved state results and/or separate result files;
- already obtained LLM results must not be lost on repeated runs.

The implementation may store results:

1. directly in the state file;
2. in separate files per batch;
3. in `04_llm_classified.jsonl` with subsequent deduplication by SHA.

The main requirement: a repeated run must continue without losing results.

---

### 9.10. Running the LLM and handling invalid JSON

LLM analysis is run once for each batch, but the operation must be resumable according to section 9.9.

If the LLM response is not valid JSON, the implementation must first try to extract JSON from a Markdown code block.

Supported variants:

````markdown
```json
{ ... }
```
````

and:

````markdown
```
{ ... }
```
````

Only if JSON is still invalid after such cleanup:

1. Repeat the same batch once.
2. If the retry is also invalid, all commits in the batch are marked as:

```json
{
  "category": "unclear",
  "candidate_for_changelog": false,
  "changelog_line": null,
  "comment": "LLM returned invalid JSON for this batch.",
  "confidence": "low"
}
```

Such a batch is considered successfully completed with fallback results and receives status `done`.

---

### 9.11. Prompt contract

The LLM must receive the instruction:

```text
You help prepare a CHANGELOG for a C++ framework/library.

You are given a list of commits between releases. For each commit, decide:
1. Whether it should be included in the CHANGELOG.
2. Which category it belongs to.
3. If it should be included, write a short CHANGELOG line in English.
4. Provide a short comment for the maintainer.

Important:
- Do not invent facts that are not present in the commit subject/body/files.
- If unsure, use category = "unclear".
- Every input SHA must be returned exactly once.
- Do not skip commits.
- Do not add SHA values that were not present in the input.

Categories:
- feature
- improvement
- breaking_change
- important_bugfix
- internal
- docs
- tests
- chore
- unclear

Rules for inclusion in CHANGELOG:
Include the commit if it adds public functionality, changes behavior,
adds a noticeable improvement, contains a breaking change, an important bugfix,
or an important build/platform change.

Do not include it if it is only internal refactoring, tests, CI, formatting,
minor docs, or cleanup.

Response format — strict JSON:

{
  "items": [
    {
      "sha": "...",
      "category": "feature",
      "candidate_for_changelog": true,
      "changelog_line": "Added support for ...",
      "comment": "Why this was classified this way.",
      "confidence": "high"
    }
  ],
  "stats": {
    "input_count": 0,
    "output_count": 0
  }
}

Commits:
<COMMITS_JSON>
```

---

### 9.12. Invariants

For each LLM batch:

- `stats.input_count` must match the number of commits in the batch.
- `stats.output_count` must match the number of elements in `items`.
- Each SHA from the batch must be returned exactly once.
- There must be no SHA values that were not in the batch.

If the LLM violates these invariants:

1. The response is considered invalid.
2. The logic from section 9.10 is applied.
3. After a repeated failure, fallback classification `unclear` is created for all commits in the batch.

---

## 10. Stage 5: Verification and Preparation of Review Data

The stage consists of two parts:

1. Machine verification.
2. Preparation of data for human review.

---

## 10.1. Stage 5.1: Verification

### Goal

Verify that the pipeline has not lost any commit.

---

### Input

```text
.changelog/01_commits_manifest.json
.changelog/03_preclassified.jsonl
.changelog/04_llm_classified.jsonl
```

---

### Output

```text
.changelog/05_verified_classification.jsonl
.changelog/05_verification_report.json
```

---

### Merge rules

For each SHA from the original list:

- if `auto_classification.send_to_llm == false`, use `auto_classification`;
- if `auto_classification.send_to_llm == true`, use `llm_classification`.

Final object:

```json
{
  "sha": "991c62abad9ad065cb0af7388616ed340c5ff57a",
  "short_sha": "991c62ab",

  "subject": "Add support for Redis pipeline",
  "body": "Long commit message...",
  "commit_url": "https://github.com/userver-framework/userver/commit/991c62abad9ad065cb0af7388616ed340c5ff57a",

  "github_login": "ivan-petrov",
  "github_profile_url": "https://github.com/ivan-petrov",
  "is_external": true,
  "co_authors": [],

  "changed_files": ["..."],
  "insertions": 240,
  "deletions": 31,
  "files_count": 4,
  "size_score": 271,
  "size_bucket": "large",

  "classification_source": "llm",
  "category": "feature",
  "candidate_for_changelog": true,
  "changelog_line": "Added support for Redis pipelining.",
  "classification_comment": "New user-facing Redis functionality with tests and documentation.",
  "confidence": "high"
}
```

---

### Verification report

```json
{
  "input": {
    "total_commits": 147,
    "sha_checksum": "..."
  },
  "output": {
    "total_commits": 147,
    "sha_checksum": "..."
  },
  "passed": true,
  "checks": {
    "same_sha_set": true,
    "same_checksum": true,
    "no_duplicates": true,
    "all_have_classification": true,
    "llm_output_complete": true
  },
  "stats": {
    "sent_to_llm": 32,
    "skipped_by_merge": 5,
    "skipped_by_docs": 18,
    "skipped_by_bugfix": 41,
    "skipped_by_size": 26,
    "candidates_for_changelog": 17,
    "external_contributors": 4
  }
}
```

---

### Mandatory checks

- The SHA set in `05_verified_classification.jsonl` equals the SHA set from `01_commits_manifest.json`.
- Checksum matches.
- There are no duplicate SHA values.
- Every commit has a category.
- Every commit sent to the LLM has an LLM classification result or fallback classification.
- `04_llm_classification_state.json` does not contain unfinished mandatory batches if there are commits sent to the LLM.

If any mandatory check fails, the `review` command must terminate with an error.

---

## 10.2. Stage 5.2: Review Data for Maintainer

### Goal

Prepare data that allows the maintainer to understand:

- which commits were in the range;
- which commits were filtered out by heuristics;
- which commits were analyzed by the LLM;
- which commits are proposed for inclusion in the `CHANGELOG`;
- which commits require attention.

---

### Output

```text
.changelog/05_review_report.md
.changelog/05_review_overrides.yaml
```

---

### 10.2.1. Human-readable format

The exact human-readable report format is not fixed by this specification and must be designed separately.

Markdown is acceptable for MVP.

Important: the implementation must preserve and show in the report all data required for review.

Minimum required data set:

- full SHA;
- short SHA;
- commit URL;
- commit subject;
- commit body/message;
- author name;
- author email;
- GitHub login;
- GitHub profile URL;
- external contributor flag;
- co-authors;
- changed files;
- insertions;
- deletions;
- files count;
- size score;
- size bucket;
- classification source;
- category;
- candidate for changelog;
- changelog line;
- classification comment;
- confidence;
- warnings / attention flags.

---

### 10.2.2. Sorting for review

The review report must provide a way to view all commits sorted by size:

```text
size_score DESC
```

That is, the largest commits must be the most visible.

---

### 10.2.3. Attention flags

The implementation must mark commits requiring attention:

- `category == unclear`;
- `confidence == low`;
- `candidate_for_changelog == true`, but `changelog_line` is empty;
- `github_login == UNKNOWN`;
- `is_external == true`, but `github_profile_url == null`;
- there is a co-author with `github_login == UNKNOWN`;
- there is a co-author with `is_external == true`, but `github_profile_url == null`;
- `is_merge_commit == true`;
- commit was too large for the LLM and received fallback `unclear`.

Additional flags may be added.

---

### 10.2.4. Override file

The maintainer must not edit JSONL.

Manual corrections are made using:

```text
.changelog/05_review_overrides.yaml
```

The `review` command must handle this file safely.

If `05_review_overrides.yaml` does not exist, the command creates a new file.

If `05_review_overrides.yaml` already exists, the command must not silently overwrite it.

In that case, the command must offer the user one of the following actions:

1. `keep` — leave the file unchanged;
2. `merge` — add missing SHA templates while preserving all existing user overrides;
3. `backup-and-regenerate` — save the current file as `.bak` and create a new one.

Default action when there is no interactive input:

```text
keep
```

Example:

```yaml
overrides:
  991c62abad9ad065cb0af7388616ed340c5ff57a:
    category: feature
    candidate_for_changelog: true
    changelog_line: "Added support for Redis pipelining."
    maintainer_comment: "Good to mention in release notes."

  deadbeefdeadbeefdeadbeefdeadbeefdeadbeef:
    candidate_for_changelog: false
    maintainer_comment: "Internal only."
```

Override may change:

- `category`;
- `candidate_for_changelog`;
- `changelog_line`;
- `maintainer_comment`.

---

## 11. Stage 6: Generate CHANGELOG Items

### 11.1. Goal

Create a machine-readable list of items that will be included in the proposed `CHANGELOG`.

---

### 11.2. Input

```text
.changelog/05_verified_classification.jsonl
.changelog/05_review_overrides.yaml
```

---

### 11.3. Output

```text
.changelog/06_changelog_items.jsonl
.changelog/06_changelog_line_generation_state.json
```

---

### 11.4. Logic

1. Read `05_verified_classification.jsonl`.
2. Apply overrides from `05_review_overrides.yaml`.
3. Select commits for which:

```json
"candidate_for_changelog": true
```

4. For candidates without `changelog_line`, generate the line through a separate resumable LLM process.
5. Write final items to `06_changelog_items.jsonl`.

---

### 11.5. Item format

```json
{
  "sha": "991c62abad9ad065cb0af7388616ed340c5ff57a",
  "short_sha": "991c62ab",
  "category": "feature",
  "changelog_section": "Functionality",
  "changelog_line": "Added support for Redis pipelining.",
  "author_login": "ivan-petrov",
  "author_profile_url": "https://github.com/ivan-petrov",
  "co_authors": [
    {
      "login": "petr-sidorov",
      "profile_url": "https://github.com/petr-sidorov",
      "is_external": true
    }
  ],
  "is_external": true,
  "commit_url": "https://github.com/userver-framework/userver/commit/991c62abad9ad065cb0af7388616ed340c5ff57a",
  "source": "llm",
  "maintainer_comment": null,
  "attention_flags": []
}
```

---

### 11.6. Mapping categories to CHANGELOG sections

```text
breaking_change  -> Breaking changes
feature          -> Functionality
improvement      -> Improvements
important_bugfix -> Bug fixes
docs             -> Documentation
```

The following categories are not included by default:

```text
internal
tests
chore
unclear
small
minor_bugfix
```

The category `minor_bugfix` means a small bugfix filtered out by a heuristic.

The category `important_bugfix` means a bugfix that the LLM or maintainer considers significant for users.

If the maintainer sets `candidate_for_changelog: true` via override, but the category does not map to a standard section, the item goes to:

```text
Other
```

---

### 11.7. Generating missing CHANGELOG lines

If a selected candidate has:

```text
candidate_for_changelog == true
changelog_line == null or empty string
```

then the tool may make a separate LLM request to generate the line.

This process must be resumable.

For each such SHA, the tool must store generation status in:

```text
.changelog/06_changelog_line_generation_state.json
```

Example:

```json
{
  "operation": "changelog_line_generation",
  "input_checksum": "sha256-of-selected-candidate-sha-list",
  "items": {
    "991c62abad9ad065cb0af7388616ed340c5ff57a": {
      "status": "done",
      "prompt_hash": "sha256-of-prompt",
      "attempts": 1,
      "changelog_line": "Added support for Redis pipelining.",
      "updated_at": "2026-06-08T12:00:00Z"
    },
    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef": {
      "status": "failed",
      "prompt_hash": "sha256-of-prompt",
      "attempts": 2,
      "error": "LLM request failed: timeout",
      "updated_at": "2026-06-08T12:03:00Z"
    }
  }
}
```

Allowed statuses:

```text
pending
in_progress
done
failed
skipped
```

Requirements:

- before sending an item to the LLM, its status must become `in_progress`;
- after successful generation, the status must become `done`;
- already successfully generated lines must not be generated again;
- if the network or LLM fails, the next `report` run must continue from the first `pending`, `failed`, or stale `in_progress` item;
- `in_progress` left after a process crash must be considered stale and may be restarted;
- the user must see progress in the console;
- if a line could not be generated, the final report must still be created, but the item must receive an attention flag.

Fallback for a missing line:

```text
[TODO: write changelog line] <commit subject>
```

This fallback must be clearly marked as TODO and must not look like a final ready-to-use line.

---

### 11.8. Prompt for generating a missing CHANGELOG line

Input to the LLM:

- subject;
- body;
- changed files;
- category;
- maintainer comment.

Requirements for the result:

- one short line in English;
- no Markdown bullets;
- no links;
- no invented facts.

If the LLM returns a Markdown block or JSON wrapper, the implementation must try to extract the useful text.

If the LLM cannot generate the line after the allowed number of attempts, the fallback from section 11.7 is used.

---

## 12. Stage 7: Final Report

### 12.1. Goal

Generate a human-readable final report containing:

- proposed `CHANGELOG`;
- list of external contributors;
- statistics;
- warnings;
- appendix with all commits.

---

### 12.2. Input

```text
.changelog/06_changelog_items.jsonl
.changelog/05_verified_classification.jsonl
.changelog/02_external_contributors.md
.changelog/05_verification_report.json
.changelog/06_changelog_line_generation_state.json
```

---

### 12.3. Output

```text
.changelog/07_final_report.md
```

---

### 12.4. Human-readable format

The exact layout of the final report is not fixed by this specification and must be designed separately.

Markdown is acceptable for MVP.

The report must contain:

1. Proposed CHANGELOG.
2. External contributors.
3. Verification status.
4. Statistics.
5. Human attention items.
6. Appendix with all commits.

The final report must explicitly show items with fallback CHANGELOG lines.

For example:

```markdown
- [TODO: write changelog line] Add Redis pipeline support ([`991c62ab`](...))
```

Such items must also be included in the Human attention items section.

---

## 13. CLI Interface

The tool must provide three main commands.

---

### 13.1. `collect`

```bash
changelog-tool collect
```

or:

```bash
changelog-tool collect --from v2.5.0 --to HEAD
```

Runs stages:

```text
1, 2, 3, 4
```

Creates:

```text
.changelog/01_commits.jsonl
.changelog/01_commits_manifest.json
.changelog/02_commits_with_contributors.jsonl
.changelog/02_external_contributors.md
.changelog/03_preclassified.jsonl
.changelog/04_llm_classified.jsonl
.changelog/04_llm_classification_state.json
```

Example terminal output:

```text
Collecting commits v2.5.0..HEAD

Commits: 147
External contributors: 4

Heuristics:
  merge commits: 5
  docs by subject: 18
  fix/bug under 200 lines: 41
  small under 50 lines: 26

Sent to LLM: 32
LLM batches:
  done: 2
  failed: 0
  pending: 0

Done.

Next:
  changelog-tool review
```

If the command continues after interruption:

```text
Resuming LLM classification...

Batches:
  already done: 1
  to process: 1

Processing batch-0002...
Done.

Next:
  changelog-tool review
```

---

### 13.2. `review`

```bash
changelog-tool review
```

Runs stage:

```text
5
```

Creates:

```text
.changelog/05_verified_classification.jsonl
.changelog/05_verification_report.json
.changelog/05_review_report.md
.changelog/05_review_overrides.yaml
```

Example terminal output:

```text
Verification: PASSED

Generated:
  .changelog/05_verified_classification.jsonl
  .changelog/05_verification_report.json
  .changelog/05_review_report.md

Overrides:
  .changelog/05_review_overrides.yaml already exists
  Action: keep

Open:
  .changelog/05_review_report.md

If needed, edit:
  .changelog/05_review_overrides.yaml

Then run:
  changelog-tool report
```

---

### 13.3. `report`

```bash
changelog-tool report
```

Runs stages:

```text
6, 7
```

Creates:

```text
.changelog/06_changelog_items.jsonl
.changelog/06_changelog_line_generation_state.json
.changelog/07_final_report.md
```

Example terminal output:

```text
Applied overrides: 3

Generating missing changelog lines:
  already done: 1
  to process: 2

  [1/2] 991c62ab ... done
  [2/2] deadbeef ... failed, using TODO fallback

Final report:
  .changelog/07_final_report.md

Included in CHANGELOG: 19
External contributors: 4
Human attention items: 1
```

---

## 14. Errors and Failure Behavior

### 14.1. Lost SHA

If at any stage the SHA set does not match the original one:

```text
ERROR: SHA set mismatch
Missing SHA:
- ...
Extra SHA:
- ...
```

The command must terminate with an error.

---

### 14.2. Duplicate SHA values

If a duplicate is found:

```text
ERROR: duplicate SHA found
- ...
```

The command must terminate with an error.

---

### 14.3. Invalid LLM JSON

For a batch:

1. Try to extract JSON from a Markdown code block.
2. If that fails, repeat the request once.
3. If the retry also fails, mark all commits in the batch as `unclear`.

The pipeline must not fail completely only because of invalid LLM JSON if fallback successfully created `unclear` classifications.

The batch state must be saved in the state file.

---

### 14.4. Unknown GitHub login

If GitHub login is not found:

- set `github_login = UNKNOWN`;
- set `is_external = true`;
- add an attention flag;
- continue execution.

This behavior applies both to the author and co-authors.

---

### 14.5. Invalid override YAML

If `05_review_overrides.yaml` cannot be parsed:

```text
ERROR: invalid overrides YAML
File: .changelog/05_review_overrides.yaml
Line: ...
```

The `report` command must terminate with an error.

---

### 14.6. Override with unknown SHA

If an override references a SHA that is not present in the original manifest:

```text
ERROR: override references unknown SHA
- ...
```

The `report` command must terminate with an error.

---

### 14.7. Existing override file

If `05_review_overrides.yaml` already exists, the `review` command must not silently overwrite it.

In interactive mode, the command must ask for an action:

- `keep`;
- `merge`;
- `backup-and-regenerate`.

In non-interactive mode, the default is:

```text
keep
```

---

### 14.8. LLM classification failure

If LLM classification is interrupted:

- already completed batches must remain saved;
- the state file must reflect the current status;
- the next `collect` run must continue processing unfinished batches;
- the pipeline must not delete already obtained results.

If a batch cannot be processed correctly after the allowed attempts, all commits in the batch receive fallback `unclear`, and the batch is considered `done`.

---

### 14.9. CHANGELOG line generation failure

If at the `report` stage a CHANGELOG line could not be generated through the LLM:

- the item is not deleted;
- progress is saved in the state file;
- the item receives a fallback line:

```text
[TODO: write changelog line] <commit subject>
```

- the item goes into Human attention;
- the next `report` run must be able to continue generation.

---

### 14.10. Single commit too large for LLM

If a single commit as an indivisible atom does not fit into `llm.max_prompt_chars`:

- it is not sent to the LLM;
- it receives fallback classification `unclear`;
- it receives an attention flag;
- the pipeline continues.

---

## 15. Non-Functional Requirements

### 15.1. Idempotency

A repeated command run with the same input data must produce the same result as much as possible.

Exceptions:

- the LLM may produce slightly different wording if the request is executed again;
- timestamps may differ.

Thanks to state files, already obtained LLM results must not be recreated unnecessarily.

---

### 15.2. Restart

If a command was interrupted, a repeated run must either:

- continue correctly;
- or fail safely with a clear error.

For LLM operations, continuation is required, not a full restart.

---

### 15.3. Caching

It is recommended to cache:

- GitHub API responses by SHA;
- LLM responses by batch prompt hash.

Cache does not replace state files.

State files are used for correct pipeline continuation.  
Cache is only an optimization.

---

### 15.4. Logs

Each command must write a clear log to:

```text
.changelog/logs/
```

Minimum:

- which input files were read;
- which output files were created;
- how many commits were processed;
- how many were sent to the LLM;
- how many LLM batches were already ready;
- how many LLM batches were processed during the current run;
- how many external contributors were found;
- warnings and errors.

---

### 15.5. Read-only access to git repository

The tool must not modify git history, create commits, create tags, or push changes.

---

### 15.6. Protection of user files

Files intended for manual editing must not be silently overwritten.

This includes at minimum:

```text
.changelog/05_review_overrides.yaml
```

Any regeneration operation must either:

- preserve the file unchanged;
- perform a merge without losing user data;
- create a backup before overwriting.

---

### 15.7. Resumability of all LLM operations

All LLM operations must be designed so they can continue after a failure.

Minimum required LLM operations:

1. Commit classification at stage 4.
2. Generation of missing `CHANGELOG` lines at stage 6.

For each LLM operation there must be:

- a state file;
- statuses `pending`, `in_progress`, `done`, `failed`, `skipped`;
- saving successful results immediately after receiving them;
- ability to rerun without losing already completed results;
- clear progress in the console.

---

## 16. Explicitly Out of Scope

The tool must not:

- determine the next version number;
- automatically publish a GitHub Release;
- automatically push changes;
- automatically send messages to authors;
- guarantee perfect `CHANGELOG` text without maintainer review;
- fully replace manual release review;
- fix the exact layout of human-readable reports;
- fix a concrete LLM provider or connection mechanism.

---

## 17. Minimal Successful Scenario

```bash
# 1. Prepare config
cat changelog.yaml

# 2. Run collection and LLM analysis
changelog-tool collect

# 3. Generate review data
changelog-tool review

# 4. Maintainer reads:
#    .changelog/05_review_report.md
#
#    If needed, edits:
#    .changelog/05_review_overrides.yaml

# 5. Generate final report
changelog-tool report

# 6. Maintainer opens:
#    .changelog/07_final_report.md
```

If `collect` or `report` was interrupted during LLM operations, it is enough to run the same command again:

```bash
changelog-tool collect
# or
changelog-tool report
```

The tool must continue from where it stopped.

---

## 18. MVP Readiness Criteria

MVP is considered ready if:

1. It is possible to specify `from` and `to` refs.
2. The tool collects all commits in the range, including merge commits.
3. The tool forms a SHA checksum.
4. The tool parses `Co-authored-by`.
5. The tool identifies external contributors among authors and co-authors.
6. The core-team list is taken from `changelog.yaml`.
7. The tool applies four heuristics:
   - merge commits;
   - docs by subject;
   - fix/bug by subject + size;
   - small size.
8. The tool sends remaining commits to the LLM in batches.
9. LLM classification is resumable.
10. The tool can extract JSON from a Markdown code block in an LLM response.
11. The tool verifies that all SHA values are preserved.
12. The tool creates a review report.
13. The tool creates and safely handles `05_review_overrides.yaml`.
14. The tool accepts manual overrides.
15. The tool creates a final report with the proposed `CHANGELOG`.
16. Generation of missing CHANGELOG lines is resumable.
17. External contributors are present in the final report.
18. If a SHA is lost, the pipeline fails with an error.
19. If a GitHub login is unknown, the participant is considered external and goes into attention.
20. If a single commit does not fit into the LLM prompt, it is marked as `unclear`, and the pipeline continues.

---

## 19. Final Artifact Architecture

On a standard run, the tool creates the structure:

```text
.changelog/
├── 01_commits.jsonl
├── 01_commits_manifest.json
│
├── 02_commits_with_contributors.jsonl
├── 02_external_contributors.md
│
├── 03_preclassified.jsonl
│
├── 04_llm_classified.jsonl
├── 04_llm_classification_state.json
│
├── 05_verified_classification.jsonl
├── 05_verification_report.json
├── 05_review_report.md
├── 05_review_overrides.yaml
│
├── 06_changelog_items.jsonl
├── 06_changelog_line_generation_state.json
│
├── 07_final_report.md
│
└── logs/
```

Purpose of key files:

| File | Purpose |
|---|---|
| `01_commits.jsonl` | Full list of commits from git |
| `01_commits_manifest.json` | SHA manifest and checksum |
| `02_commits_with_contributors.jsonl` | Commits with GitHub authors/co-authors |
| `02_external_contributors.md` | List of external contributors |
| `03_preclassified.jsonl` | Result of heuristics |
| `04_llm_classified.jsonl` | Result of LLM classification |
| `04_llm_classification_state.json` | State of resumable LLM classification |
| `05_verified_classification.jsonl` | Unified classification after merge |
| `05_verification_report.json` | Report on invariant checks |
| `05_review_report.md` | Report for the maintainer |
| `05_review_overrides.yaml` | Manual maintainer overrides |
| `06_changelog_items.jsonl` | Final proposed CHANGELOG items |
| `06_changelog_line_generation_state.json` | State of missing line generation |
| `07_final_report.md` | Final human-readable report |

---

This is the full corrected architecture specification. It describes why the tool is needed, how it works, which stages exist, which artifacts are created, how the human, git, GitHub, and LLM interact, which invariants must be maintained, how user data is protected, and how all LLM operations continue after failures.