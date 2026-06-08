"""Stage 7: Final report generation (Spec §12).

This module generates the final human-readable report (``07_final_report.md``)
containing the proposed CHANGELOG, external contributors, statistics, and
attention items.
"""

from __future__ import annotations

from typing import Any, Dict, List

from changelog_tool.llm_generator import TODO_FALLBACK_PREFIX
from changelog_tool.models import ChangelogItem, ChangelogSection, VerifiedCommit


def generate_final_report(
    items: List[ChangelogItem],
    all_commits: List[VerifiedCommit],
    external_contributors_md: str,
    verification_report: Dict[str, Any],
    output_path: str,
) -> None:
    """Generate the final Markdown report (Spec §12.4).

    Args:
        items:                    List of proposed CHANGELOG items.
        all_commits:              List of all verified commits (for the appendix).
        external_contributors_md: Content of ``02_external_contributors.md``.
        verification_report:      Parsed ``05_verification_report.json``.
        output_path:              Path to write ``07_final_report.md``.
    """
    lines: List[str] = []
    lines.append("# Final Release Report\n")

    # --- 1. Proposed CHANGELOG ---
    lines.append("## 1. Proposed CHANGELOG\n")

    # Group items by section
    sections: Dict[ChangelogSection, List[ChangelogItem]] = {
        sec: [] for sec in ChangelogSection
    }
    for item in items:
        sections[item.changelog_section].append(item)

    has_any_items = False
    for section in ChangelogSection:
        section_items = sections[section]
        if not section_items:
            continue

        has_any_items = True
        lines.append(f"### {section.value}\n")
        for item in section_items:
            # Format: - Changelog line ([`short_sha`](url))
            line_text = item.changelog_line.strip()
            lines.append(f"- {line_text} ([`{item.short_sha}`]({item.commit_url}))")
        lines.append("")

    if not has_any_items:
        lines.append("*No items selected for the CHANGELOG.*\n")

    # --- 2. External Contributors ---
    lines.append("## 2. External Contributors\n")
    if external_contributors_md.strip():
        lines.append(external_contributors_md.strip())
    else:
        lines.append("*No external contributors found.*")
    lines.append("")

    # --- 3. Verification Status ---
    lines.append("## 3. Verification Status\n")
    passed = verification_report.get("passed", False)
    status_str = "✅ PASSED" if passed else "❌ FAILED"
    lines.append(f"**Status:** {status_str}\n")

    checks = verification_report.get("checks", {})
    for check_name, check_passed in checks.items():
        icon = "✅" if check_passed else "❌"
        lines.append(f"- {icon} `{check_name}`")
    lines.append("")

    # --- 4. Statistics ---
    lines.append("## 4. Statistics\n")
    stats = verification_report.get("stats", {})
    lines.append(f"- **Total commits:** {verification_report.get('output', {}).get('total_commits', 0)}")
    lines.append(f"- **Sent to LLM:** {stats.get('sent_to_llm', 0)}")
    lines.append(f"- **Candidates for CHANGELOG:** {stats.get('candidates_for_changelog', 0)}")
    lines.append(f"- **External contributors:** {stats.get('external_contributors', 0)}")
    lines.append("")

    # --- 5. Human Attention Items ---
    lines.append("## 5. Human Attention Items\n")
    attention_items = []

    # Find items with TODO fallback lines
    for item in items:
        if item.changelog_line.startswith(TODO_FALLBACK_PREFIX):
            attention_items.append(
                f"- ⚠️ **Missing CHANGELOG line:** [`{item.short_sha}`]({item.commit_url}) — {item.changelog_line}"
            )

    # Find commits with attention flags
    for c in all_commits:
        if c.attention_flags:
            flags_str = ", ".join(f"`{f}`" for f in c.attention_flags)
            attention_items.append(
                f"- ⚠️ **Attention flags:** [`{c.short_sha}`]({c.commit_url}) — {flags_str}"
            )

    if attention_items:
        lines.extend(attention_items)
    else:
        lines.append("*No items require attention.*")
    lines.append("")

    # --- 6. Appendix: All Commits ---
    lines.append("## 6. Appendix: All Commits\n")
    lines.append("<details>")
    lines.append("<summary>Click to expand</summary>\n")

    for c in all_commits:
        lines.append(f"- [`{c.short_sha}`]({c.commit_url}) {c.subject}")

    lines.append("\n</details>\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


__all__ = ["generate_final_report"]
