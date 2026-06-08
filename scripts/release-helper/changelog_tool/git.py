"""Git subprocess helpers for the CHANGELOG preparation tool.

Provides a read-only interface to the local git repository.  All operations
use ``git log`` / ``git show`` via :mod:`subprocess` and never modify the
repository (Spec §15.5).

Public API
----------
collect_commits(local_path, from_ref, to_ref, github_url) -> List[Commit]
    Run ``git log <from_ref>..<to_ref>`` and return fully-populated
    :class:`~changelog_tool.models.Commit` objects (stage-1 fields only;
    GitHub fields remain ``None``).

Spec references
---------------
§6.4  — fields required in 01_commits.jsonl
§6.5  — merge commits (parents_count > 1), no --no-merges
§5.2  — size_score = insertions + deletions
§5.3  — size_bucket thresholds
§5.5  — Co-authored-by trailer parsing
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from changelog_tool.models import (
    CoAuthor,
    Commit,
    compute_size_bucket,
    compute_size_score,
)

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class GitError(Exception):
    """Raised when a git subprocess call fails or the repo is invalid."""


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Separator that is extremely unlikely to appear in commit messages.
_RECORD_SEP = "\x1e"  # ASCII Record Separator (RS)
_FIELD_SEP = "\x1f"   # ASCII Unit Separator (US)

# git log format: fields separated by _FIELD_SEP, records by _RECORD_SEP.
# Fields (in order):
#   0  full SHA
#   1  abbreviated SHA (8 chars)
#   2  parent SHAs (space-separated; empty for root commits)
#   3  author name
#   4  author email
#   5  raw commit body (subject + blank line + body)
_LOG_FORMAT = (
    f"%H{_FIELD_SEP}"
    f"%h{_FIELD_SEP}"
    f"%P{_FIELD_SEP}"
    f"%aN{_FIELD_SEP}"
    f"%aE{_FIELD_SEP}"
    f"%B"
    f"{_RECORD_SEP}"
)

# Regex for Co-authored-by trailers (Spec §5.5).
_CO_AUTHOR_RE = re.compile(
    r"^Co-authored-by:\s*(.+?)\s*<([^>]+)>\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Regex for the numstat diff line: "<insertions>\t<deletions>\t<filename>"
# Deletions or insertions may be "-" for binary files.
_NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def collect_commits(
    local_path: str,
    from_ref: str,
    to_ref: str,
    github_url: str,
) -> List[Commit]:
    """Collect all commits in ``<from_ref>..<to_ref>`` from *local_path*.

    Args:
        local_path:  Path to the local git clone (``repo.local_path``).
        from_ref:    Previous release tag or SHA (exclusive lower bound).
        to_ref:      Current tag, SHA, branch, or ``HEAD`` (inclusive upper).
        github_url:  Base URL of the repository (used to build commit URLs).

    Returns:
        List of :class:`~changelog_tool.models.Commit` objects in the order
        returned by ``git log`` (newest first).  Stage-2 GitHub fields are
        ``None``; stage-3/4 classification fields are ``None``.

    Raises:
        :class:`GitError`: If the git command fails or the path is not a repo.
    """
    _validate_repo(local_path)
    raw_commits = _run_git_log(local_path, from_ref, to_ref)
    diff_map = _run_git_numstat(local_path, from_ref, to_ref)

    commits: List[Commit] = []
    for sha, short_sha, parents_raw, author_name, author_email, message in raw_commits:
        subject, body = _split_message(message)

        insertions, deletions, changed_files = diff_map.get(sha, (0, 0, []))

        size_score = compute_size_score(insertions, deletions)

        commits.append(
            Commit(
                sha=sha,
                short_sha=short_sha,
                is_merge_commit=len(parents_raw.split()) > 1 if parents_raw.strip() else False,
                author_name=author_name,
                author_email=author_email,
                co_authors=_parse_co_authors(body),
                subject=subject,
                body=body,
                message=message.rstrip("\n"),
                commit_url=f"{github_url.rstrip("/")}/commit/{sha}",
                changed_files=changed_files,
                insertions=insertions,
                deletions=deletions,
                files_count=len(changed_files),
                size_score=size_score,
                size_bucket=compute_size_bucket(size_score),
            )
        )

    return commits


# ---------------------------------------------------------------------------
# Git subprocess helpers
# ---------------------------------------------------------------------------


def _validate_repo(local_path: str) -> None:
    """Raise :class:`GitError` if *local_path* is not a git repository."""
    if not os.path.isdir(local_path):
        raise GitError(f"local_path does not exist or is not a directory: {local_path}")
    result = _git(
        ["rev-parse", "--git-dir"],
        local_path,
        check=False,
    )
    if result.returncode != 0:
        raise GitError(
            f"Not a git repository (or git not found): {local_path}\n"
            f"{result.stderr.strip()}"
        )


def _run_git_log(
    local_path: str,
    from_ref: str,
    to_ref: str,
) -> List[Tuple[str, str, str, str, str, str]]:
    """Run ``git log`` and return a list of raw field tuples.

    Each tuple: (sha, short_sha, parents_raw, author_name, author_email, message)
    """
    result = _git(
        [
            "log",
            f"--format={_LOG_FORMAT}",
            f"{from_ref}..{to_ref}",
        ],
        local_path,
    )

    raw = result.stdout
    records = []
    for block in raw.split(_RECORD_SEP):
        block = block.strip()
        if not block:
            continue
        parts = block.split(_FIELD_SEP, 5)
        if len(parts) < 6:
            # Malformed record — skip (should not happen with our format string)
            raise GitError(f"Malformed git log record: {block}")
        sha, short_sha, parents_raw, author_name, author_email, message = parts
        records.append((
            sha.strip(),
            short_sha.strip(),
            parents_raw.strip(),
            author_name.strip(),
            author_email.strip(),
            message,
        ))
    return records


def _run_git_numstat(
    local_path: str,
    from_ref: str,
    to_ref: str,
) -> dict:
    """Run ``git log --numstat`` and return a dict mapping SHA → (ins, del, files).

    For merge commits git numstat may return empty stats; we default to (0, 0, []).
    Binary files report "-" for insertions/deletions; we treat them as 0.
    """
    result = _git(
        [
            "log",
            "--numstat",
            "--format=%H",
            f"{from_ref}..{to_ref}",
        ],
        local_path,
    )

    diff_map: dict = {}
    current_sha: Optional[str] = None
    insertions = 0
    deletions = 0
    files: List[str] = []

    for line in result.stdout.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # A 40-char hex string on its own line is a SHA header from --format=%H
        if re.fullmatch(r"[0-9a-f]{40}", line_stripped):
            if current_sha is not None:
                diff_map[current_sha] = (insertions, deletions, files)
            current_sha = line_stripped
            insertions = 0
            deletions = 0
            files = []
            continue

        m = _NUMSTAT_RE.match(line_stripped)
        if m and current_sha is not None:
            ins_raw, del_raw, filename = m.group(1), m.group(2), m.group(3)
            insertions += int(ins_raw) if ins_raw != "-" else 0
            deletions += int(del_raw) if del_raw != "-" else 0
            # Handle rename notation "old => new" or "{old => new}/suffix"
            files.append(_normalise_filename(filename))

    if current_sha is not None:
        diff_map[current_sha] = (insertions, deletions, files)

    return diff_map


def _git(
    args: List[str],
    cwd: str,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a git command in *cwd* and return the completed process.

    Args:
        args:  Arguments after ``git`` (e.g. ``["log", "--format=..."]``).
        cwd:   Working directory (the local git clone).
        check: If ``True`` (default), raise :class:`GitError` on non-zero exit.

    Raises:
        :class:`GitError`: On non-zero exit when *check* is ``True``, or if
            the ``git`` executable is not found.
    """
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found. Is git installed?") from exc

    if check and result.returncode != 0:
        raise GitError(
            f"git command failed (exit {result.returncode}):\n"
            f"  command: {' '.join(cmd)}\n"
            f"  stderr:  {result.stderr.strip()}"
        )
    return result


# ---------------------------------------------------------------------------
# Message parsing helpers
# ---------------------------------------------------------------------------


def _split_message(message: str) -> Tuple[str, str]:
    """Split a raw commit message into (subject, body).

    The subject is the first non-empty line.  The body is everything after
    the first blank line (or empty string if there is no body).
    """
    lines = message.split("\n")
    subject = ""
    body_lines: List[str] = []
    past_subject = False

    for line in lines:
        if not past_subject:
            stripped = line.strip()
            if stripped:
                subject = stripped
                past_subject = True
        else:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()
    return subject, body


def _parse_co_authors(body: str) -> List[CoAuthor]:
    """Extract ``Co-authored-by:`` trailers from the commit body (Spec §5.5)."""
    co_authors: List[CoAuthor] = []
    for match in _CO_AUTHOR_RE.finditer(body):
        name = match.group(1).strip()
        email = match.group(2).strip()
        co_authors.append(CoAuthor(name=name, email=email))
    return co_authors


def _normalise_filename(filename: str) -> str:
    """Normalise a filename from git numstat output.

    Git uses ``{old => new}`` or ``old/path => new/path`` notation for renames.
    We keep the destination (right-hand side) path.
    """
    # Pattern: "prefix/{old => new}/suffix" or "old => new"
    brace_re = re.compile(r"\{([^}]*) => ([^}]*)\}")
    m = brace_re.search(filename)
    if m:
        prefix = filename[: m.start()]
        suffix = filename[m.end() :]
        new_part = m.group(2)
        return (prefix + new_part + suffix).replace("//", "/")

    arrow_re = re.compile(r"^(.*) => (.*)$")
    m2 = arrow_re.match(filename)
    if m2:
        return m2.group(2).strip()

    return filename


# ---------------------------------------------------------------------------
# Utility: current UTC timestamp
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (e.g. ``2026-06-08T12:00:00Z``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "GitError",
    "collect_commits",
    "utc_now_iso",
]
