"""Overrides parsing and application (Spec §10.2.4, §14.5, §14.6).

This module reads ``05_review_overrides.yaml`` and applies the manual
corrections to the verified commits.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Set

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError("PyYAML is required.") from exc

from changelog_tool.models import LlmCategory, VerifiedCommit


class OverridesError(Exception):
    """Raised when the overrides file is invalid or references unknown SHAs."""


def apply_overrides(
    commits: List[VerifiedCommit],
    overrides_path: str,
) -> int:
    """Apply manual overrides to the list of verified commits in-place.

    Args:
        commits:        List of verified commits (from stage 5.1).
        overrides_path: Path to ``05_review_overrides.yaml``.

    Returns:
        The number of overrides applied.

    Raises:
        OverridesError: If the YAML is invalid or references unknown SHAs.
    """
    if not os.path.exists(overrides_path):
        return 0

    try:
        with open(overrides_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise OverridesError(f"invalid overrides YAML in {overrides_path}:\n{exc}") from exc
    except OSError as exc:
        raise OverridesError(f"could not read overrides file {overrides_path}: {exc}") from exc

    if not raw:
        return 0

    if not isinstance(raw, dict):
        raise OverridesError(f"overrides file must contain a top-level mapping, got {type(raw).__name__}")

    overrides_dict = raw.get("overrides")
    if not overrides_dict:
        return 0

    if not isinstance(overrides_dict, dict):
        raise OverridesError(f"'overrides' key must be a mapping, got {type(overrides_dict).__name__}")

    # Validate SHAs (Spec §14.6)
    # YAML might parse all-digit SHAs as integers, so we convert keys to strings.
    stringified_overrides = {str(k): v for k, v in overrides_dict.items()}
    
    known_shas: Set[str] = {c.sha for c in commits}
    unknown_shas = [sha for sha in stringified_overrides if sha not in known_shas]
    if unknown_shas:
        lines = ["override references unknown SHA:"]
        for sha in unknown_shas:
            lines.append(f"- {sha}")
        raise OverridesError("\n".join(lines))

    # Apply overrides
    commit_map = {c.sha: c for c in commits}
    applied_count = 0

    for sha, override_data in stringified_overrides.items():
        if not isinstance(override_data, dict):
            raise OverridesError(f"override data for {sha} must be a mapping")

        commit = commit_map[sha]
        changed = False

        if "category" in override_data:
            raw_cat = override_data["category"]
            try:
                commit.category = LlmCategory(raw_cat)
                changed = True
            except ValueError:
                raise OverridesError(f"invalid category '{raw_cat}' for SHA {sha}")

        if "candidate_for_changelog" in override_data:
            raw_cand = override_data["candidate_for_changelog"]
            if not isinstance(raw_cand, bool):
                raise OverridesError(f"candidate_for_changelog for SHA {sha} must be a boolean")
            commit.candidate_for_changelog = raw_cand
            changed = True

        if "changelog_line" in override_data:
            raw_line = override_data["changelog_line"]
            if raw_line is not None and not isinstance(raw_line, str):
                raise OverridesError(f"changelog_line for SHA {sha} must be a string or null")
            commit.changelog_line = raw_line
            changed = True

        if "maintainer_comment" in override_data:
            raw_comment = override_data["maintainer_comment"]
            if raw_comment is not None and not isinstance(raw_comment, str):
                raise OverridesError(f"maintainer_comment for SHA {sha} must be a string or null")
            commit.classification_comment = raw_comment
            changed = True

        if changed:
            applied_count += 1

    return applied_count


__all__ = ["OverridesError", "apply_overrides"]
