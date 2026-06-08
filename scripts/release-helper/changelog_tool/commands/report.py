"""``report`` command — pipeline stages 6 and 7.

Stages:
    6. Generate CHANGELOG items (with resumable missing-line generation).
    7. Generate the final human-readable report.

Intended outputs (created by later tasks):
    .changelog/06_changelog_items.jsonl
    .changelog/06_changelog_line_generation_state.json
    .changelog/07_final_report.md

T0 provides only a stub that describes the command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from changelog_tool.commands import print_config_summary

if TYPE_CHECKING:
    from changelog_tool.cli import CliContext
    from changelog_tool.config import Config

_INTENDED_OUTPUTS = (
    "06_changelog_items.jsonl",
    "06_changelog_line_generation_state.json",
    "07_final_report.md",
)


def run_report(ctx: "CliContext") -> int:
    """Run the ``report`` command.

    Loads the resolved configuration and dispatches to :func:`_report`.
    Pipeline logic for stages 6-7 lands in later tasks.
    """

    from changelog_tool.cli import run_with_config

    return run_with_config(ctx, _report)


def _report(ctx: "CliContext", config: "Config") -> int:
    import os
    import sys

    from changelog_tool.io import ensure_workdir, read_json, read_jsonl, write_jsonl
    from changelog_tool.llm.factory import build_client
    from changelog_tool.llm_generator import run_line_generation
    from changelog_tool.models import ChangelogItem, ChangelogSection, category_to_section, to_dict, verified_commit_from_dict
    from changelog_tool.overrides import OverridesError, apply_overrides
    from changelog_tool.report_formatter import generate_final_report

    workdir = config.output.workdir
    ensure_workdir(workdir)

    # --- Input paths ---
    verified_path = os.path.join(workdir, "05_verified_classification.jsonl")
    overrides_path = os.path.join(workdir, "05_review_overrides.yaml")
    ext_contrib_path = os.path.join(workdir, "02_external_contributors.md")
    verification_report_path = os.path.join(workdir, "05_verification_report.json")

    # --- Output paths ---
    items_path = os.path.join(workdir, "06_changelog_items.jsonl")
    state_path = os.path.join(workdir, "06_changelog_line_generation_state.json")
    final_report_path = os.path.join(workdir, "07_final_report.md")

    if not os.path.exists(verified_path):
        print(f"ERROR: required input file not found: {verified_path}", file=sys.stderr)
        print("Run `changelog-tool review` first.", file=sys.stderr)
        return 1

    # 1. Read verified commits
    raw_verified = read_jsonl(verified_path)
    commits = [verified_commit_from_dict(r) for r in raw_verified]

    # 2. Apply overrides
    try:
        applied_count = apply_overrides(commits, overrides_path)
        print(f"Applied overrides: {applied_count}")
    except OverridesError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # 3. Generate missing lines (Stage 6)
    client = build_client(
        config.llm,
        api_key=ctx.llm_api_key,
        base_url=ctx.llm_base_url,
        model=ctx.llm_model,
    )
    print("\nGenerating missing changelog lines:")
    gen_result = run_line_generation(
        commits=commits,
        client=client,
        state_path=state_path,
        max_rps=config.llm.max_rps,
        max_concurrency=config.llm.max_concurrency,
        max_429_retries=config.llm.max_429_retries,
    )
    print(f"  already done: {gen_result.items_skipped_already_done}")
    print(f"  to process:   {gen_result.total_needed}")
    print(f"  done:         {gen_result.items_done}")
    print(f"  failed:       {gen_result.items_failed}")

    # 4. Create ChangelogItems
    items: list[ChangelogItem] = []
    for c in commits:
        if not c.candidate_for_changelog:
            continue

        section = category_to_section(c.category)
        if section is None:
            section = ChangelogSection.OTHER

        item = ChangelogItem(
            sha=c.sha,
            short_sha=c.short_sha,
            category=c.category,
            changelog_section=section,
            changelog_line=c.changelog_line or "",
            author_name=c.author_name,
            author_email=c.author_email,
            co_authors=c.co_authors,
            is_external=c.is_external,
            commit_url=c.commit_url,
            source=c.classification_source,
            maintainer_comment=c.classification_comment,
            attention_flags=c.attention_flags,
        )
        items.append(item)

    write_jsonl(items_path, [to_dict(item) for item in items])

    # 5. Generate final report (Stage 7)
    ext_contrib_md = ""
    if os.path.exists(ext_contrib_path):
        with open(ext_contrib_path, "r", encoding="utf-8") as f:
            ext_contrib_md = f.read()

    verification_report = {}
    if os.path.exists(verification_report_path):
        verification_report = read_json(verification_report_path)

    generate_final_report(
        items=items,
        all_commits=commits,
        external_contributors_md=ext_contrib_md,
        verification_report=verification_report,
        output_path=final_report_path,
    )

    print(f"\nFinal report:\n  {final_report_path}")

    # Print summary
    ext_count = verification_report.get("stats", {}).get("external_contributors", 0)
    attention_count = sum(1 for c in commits if c.attention_flags) + gen_result.items_failed

    print(f"\nIncluded in CHANGELOG: {len(items)}")
    print(f"External contributors: {ext_count}")
    print(f"Human attention items: {attention_count}")

    return 0
