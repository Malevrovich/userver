# Implementation Plan: T9 — Stage 5.2: Review report and overrides

## Objective
Implement Stage 5.2 of the pipeline, which prepares human-readable review data and the overrides file for the maintainer. This is the second part of the `review` command.

## Requirements (from Spec §10.2)
1. **Markdown Report (`05_review_report.md`)**:
   - Must contain all required fields (SHA, URL, subject, body, author info, external flag, co-authors, files, size, classification, confidence, attention flags).
   - Must be sorted by `size_score DESC` (largest commits first).
   - Must clearly highlight commits requiring attention.
2. **Overrides File (`05_review_overrides.yaml`)**:
   - Used by the maintainer to manually correct categories, changelog inclusion, changelog lines, and add comments.
   - Must be generated safely: if it exists, prompt the user (in interactive mode) or default to `keep` (in non-interactive mode). Options: `keep`, `merge`, `backup-and-regenerate`.
   - Must include helpful context in comments (subject, size, GitHub link) so the maintainer knows which commit they are overriding.

## Proposed Design

### 1. Markdown Report Format
```markdown
# Review Report

## ⚠️ Requires Attention
*List of short links to commits below that have attention flags.*
- [`991c62ab`](#991c62ab) — Add support for Redis pipeline (`category_unclear`)

## All Commits (Sorted by Size)

<a id="991c62ab"></a>
### [Add support for Redis pipeline](https://github.com/...) (`991c62ab`)
**Size:** 271 lines (large) | **Author:** Ivan Petrov (external)
**Category:** `feature` | **Source:** `llm` | **Confidence:** `high`
**Include in CHANGELOG:** ✅ Yes
**Proposed Line:** Added support for Redis pipelining.
**LLM Comment:** New user-facing Redis functionality with tests and documentation.
**⚠️ Attention:** `category_unclear`

<details>
<summary>Commit Details</summary>

**Message:**
Add support for Redis pipeline...

**Changed Files (4):**
- `redis/include/userver/redis/pipeline.hpp`
...
</details>
---
```

### 2. YAML Overrides Format
We will generate a template for every commit, commented out by default. Commits with attention flags will have a prominent warning comment.

```yaml
# To override a commit, uncomment its block and change the values.
overrides:
  # Add support for Redis pipeline
  # Size: 271 lines | Link: https://github.com/.../commit/991c62ab...
  # 991c62abad9ad065cb0af7388616ed340c5ff57a:
  #   category: feature
  #   candidate_for_changelog: true
  #   changelog_line: "Added support for Redis pipelining."
  #   maintainer_comment: ""

  # ⚠️ REQUIRES ATTENTION: category_unclear
  # Fix crash in parser
  # Size: 15 lines | Link: https://github.com/.../commit/deadbeef...
  # deadbeefdeadbeefdeadbeefdeadbeefdeadbeef:
  #   category: unclear
  #   candidate_for_changelog: false
  #   changelog_line: ""
  #   maintainer_comment: ""
```

## Tasks
1. **Create `changelog_tool/review_formatter.py`**:
   - Implement Markdown generation logic.
   - Implement YAML template generation logic.
   - Implement safe file handling for the YAML file (keep/merge/backup).
2. **Update `changelog_tool/commands/review.py`**:
   - Call the formatter after verification succeeds.
   - Handle interactive prompts for existing YAML files.
3. **Create `tests/test_review_formatter.py`**:
   - Test Markdown generation.
   - Test YAML generation and merge logic.
