"""Commit → prompt-context conversion for the LLM layer.

Builds the per-commit dict that is embedded in LLM prompts (Spec §9.8).
Optionally attaches a size-bounded ``git show`` diff when
``llm.include_diff: true`` is set in the config (HTTP backend only).

The full diff is **not** passed by default (Spec §9.8).  When enabled, the
diff is truncated to ``llm.diff_max_chars`` characters and clearly marked as
truncated so the model knows it is seeing a partial view.

No I/O is performed here unless ``maybe_attach_diff`` is called.  The git
command runner is injected so tests can run offline.
"""

from __future__ import annotations

import subprocess
from typing import Any, Callable, Dict, List, Optional

from changelog_tool.models import Commit

# Type alias for the injected git runner (makes unit testing easy).
GitRunner = Callable[[List[str], str], str]


def _default_git_runner(cmd: List[str], cwd: str) -> str:
    """Run a git command and return stdout as a string.

    Raises :class:`RuntimeError` on non-zero exit.
    """
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git command failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


def commit_to_context(commit: Commit) -> Dict[str, Any]:
    """Build the prompt-context dict for a single commit (Spec §9.8).

    Contains exactly the fields the spec says to pass to the LLM:
    SHA, subject, body, changed_files, insertions, deletions, files_count,
    size_score, commit_url.

    The full diff is **not** included (Spec §9.8).  Call
    :func:`maybe_attach_diff` afterwards if ``llm.include_diff`` is enabled.

    Args:
        commit: A :class:`~changelog_tool.models.Commit` object (any stage).

    Returns:
        A plain dict suitable for JSON serialisation and embedding in a prompt.
    """
    return {
        "sha": commit.sha,
        "subject": commit.subject,
        "body": commit.body,
        "changed_files": commit.changed_files,
        "insertions": commit.insertions,
        "deletions": commit.deletions,
        "files_count": commit.files_count,
        "size_score": commit.size_score,
        "commit_url": commit.commit_url,
    }


def maybe_attach_diff(
    context: Dict[str, Any],
    commit: Commit,
    repo_path: str,
    diff_max_chars: int,
    *,
    git_runner: Optional[GitRunner] = None,
) -> Dict[str, Any]:
    """Attach a size-bounded ``git show`` diff to *context* (mutates in-place).

    Runs ``git show --stat --patch <sha>`` in *repo_path* and appends the
    output (truncated to *diff_max_chars*) under the key ``"diff"``.

    If the diff cannot be fetched (e.g. the commit is not in the local repo),
    the key is set to ``null`` and a ``"diff_error"`` key is added with the
    error message.  The pipeline continues — a missing diff is not fatal.

    Args:
        context:       The dict produced by :func:`commit_to_context`.
        commit:        The commit whose diff to fetch.
        repo_path:     Path to the local git repository.
        diff_max_chars: Maximum number of characters to include in the diff.
        git_runner:    Injected git runner for testing; uses the real subprocess
                       runner by default.

    Returns:
        The same *context* dict (mutated in-place) for convenience.
    """
    runner = git_runner or _default_git_runner
    sha = commit.sha

    try:
        raw = runner(
            ["git", "show", "--stat", "--patch", sha],
            repo_path,
        )
    except Exception as exc:  # noqa: BLE001
        context["diff"] = None
        context["diff_error"] = str(exc)
        return context

    if len(raw) > diff_max_chars:
        raw = raw[:diff_max_chars] + f"\n... [TRUNCATED at {diff_max_chars} chars]"

    context["diff"] = raw
    return context


__all__ = [
    "commit_to_context",
    "maybe_attach_diff",
    "GitRunner",
]
