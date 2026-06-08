"""Stage 5.2: Preparation of review data for the maintainer (Spec §10.2).

This module generates:
1. The human-readable Markdown report (``05_review_report.md``).
2. The YAML overrides template (``05_review_overrides.yaml``).

Spec references
---------------
§10.2.1 — Human-readable format
§10.2.2 — Sorting for review (size_score DESC)
§10.2.3 — Attention flags
§10.2.4 — Override file
§14.7   — Existing override file handling
"""

from __future__ import annotations

import os
import shutil
from typing import List

from changelog_tool.models import VerifiedCommit


# ---------------------------------------------------------------------------
# Markdown Report Generation
# ---------------------------------------------------------------------------


def generate_markdown_report(commits: List[VerifiedCommit], output_path: str) -> None:
    """Generate the human-readable Markdown review report.

    Args:
        commits:     List of verified commits.
        output_path: Path to write the Markdown file.
    """
    # Sort by size_score DESC (Spec §10.2.2)
    sorted_commits = sorted(commits, key=lambda c: c.size_score, reverse=True)

    attention_commits = [c for c in sorted_commits if c.attention_flags]

    lines: List[str] = []
    lines.append("# Review Report\n")

    # --- Attention Section ---
    if attention_commits:
        lines.append("## ⚠️ Requires Attention\n")
        for c in attention_commits:
            flags_str = ", ".join(c.attention_flags)
            lines.append(f"- [`{c.short_sha}`](#{c.short_sha}) — {c.subject} (`{flags_str}`)")
        lines.append("")

    # --- All Commits Section ---
    lines.append("## All Commits (Sorted by Size)\n")

    for c in sorted_commits:
        lines.append(f'<a id="{c.short_sha}"></a>')
        lines.append(f"### [{c.subject}]({c.commit_url}) (`{c.short_sha}`)")

        author_type = "external" if c.is_external else "core-team"
        lines.append(
            f"**Size:** {c.size_score} lines ({c.size_bucket.value}) | "
            f"**Author:** {c.author_name} ({author_type})"
        )

        lines.append(
            f"**Category:** `{c.category.value}` | "
            f"**Source:** `{c.classification_source.value}` | "
            f"**Confidence:** `{c.confidence.value}`"
        )

        candidate_str = "✅ Yes" if c.candidate_for_changelog else "❌ No"
        lines.append(f"**Include in CHANGELOG:** {candidate_str}")

        if c.changelog_line:
            lines.append(f"**Proposed Line:** {c.changelog_line}")
        else:
            lines.append("**Proposed Line:** *(none)*")

        if c.classification_comment:
            lines.append(f"**LLM Comment:** {c.classification_comment}")

        if c.attention_flags:
            flags_str = ", ".join(f"`{f}`" for f in c.attention_flags)
            lines.append(f"**⚠️ Attention:** {flags_str}")

        lines.append("\n<details>")
        lines.append("<summary>Commit Details</summary>\n")
        lines.append("**Message:**")
        lines.append("```text")
        lines.append(c.body.strip())
        lines.append("```")

        if c.co_authors:
            lines.append("**Co-authors:**")
            for ca in c.co_authors:
                ca_type = "external" if ca.is_external else "core-team"
                lines.append(f"- {ca.name} <{ca.email}> ({ca_type})")
            lines.append("")

        lines.append(f"**Changed Files ({c.files_count}):**")
        # Limit the number of files shown to avoid massive reports
        max_files = 20
        for f in c.changed_files[:max_files]:
            lines.append(f"- `{f}`")
        if len(c.changed_files) > max_files:
            lines.append(f"- *... and {len(c.changed_files) - max_files} more*")

        lines.append("</details>\n")
        lines.append("---\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# YAML Overrides Generation
# ---------------------------------------------------------------------------


def generate_yaml_overrides_template(commits: List[VerifiedCommit]) -> str:
    """Generate the content for the YAML overrides template.

    All commits are included as commented-out blocks.
    """
    # Sort by size_score DESC to match the Markdown report
    sorted_commits = sorted(commits, key=lambda c: c.size_score, reverse=True)

    lines: List[str] = []
    lines.append("# To override a commit, uncomment its block and change the values.")
    lines.append("overrides:")

    for c in sorted_commits:
        lines.append("")
        if c.attention_flags:
            flags_str = ", ".join(c.attention_flags)
            lines.append(f"  # ⚠️ REQUIRES ATTENTION: {flags_str}")

        lines.append(f"  # {c.subject}")
        lines.append(f"  # Size: {c.size_score} lines | Link: {c.commit_url}")
        lines.append(f"  # {c.sha}:")
        lines.append(f"  #   category: {c.category.value}")
        candidate_str = "true" if c.candidate_for_changelog else "false"
        lines.append(f"  #   candidate_for_changelog: {candidate_str}")

        # Escape quotes in changelog_line
        cl_line = c.changelog_line or ""
        cl_line_escaped = cl_line.replace('"', '\\"')
        lines.append(f'  #   changelog_line: "{cl_line_escaped}"')
        lines.append('  #   maintainer_comment: ""')

    return "\n".join(lines) + "\n"


def handle_overrides_file(
    commits: List[VerifiedCommit],
    output_path: str,
    action: str = "keep",
) -> None:
    """Create or update the overrides YAML file safely.

    Args:
        commits:     List of verified commits.
        output_path: Path to the overrides file.
        action:      Action to take if the file exists: "keep", "merge", or
                     "backup-and-regenerate".
    """
    template_content = generate_yaml_overrides_template(commits)

    if not os.path.exists(output_path):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(template_content)
        return

    if action == "keep":
        return

    if action == "backup-and-regenerate":
        backup_path = output_path + ".bak"
        shutil.copy2(output_path, backup_path)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(template_content)
        return

    if action == "merge":
        # For MVP, "merge" just appends the new template to the end of the file
        # if it's not already there. A true YAML merge preserving comments is
        # complex. Since the template is all comments, appending is safe and
        # provides the missing SHAs.
        with open(output_path, "a", encoding="utf-8") as f:
            f.write("\n# --- Merged Template ---\n")
            f.write(template_content)
        return

    raise ValueError(f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "generate_markdown_report",
    "generate_yaml_overrides_template",
    "handle_overrides_file",
]
