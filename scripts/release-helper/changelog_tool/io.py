"""File I/O utilities for the CHANGELOG preparation tool.

Provides:
    - SHA checksum computation (Spec §6.7)
    - JSONL read/write helpers
    - Atomic JSON file write (crash-safe state files)
    - Workdir initialisation

This module has no dependencies outside the Python standard library and must
not import from any other changelog_tool module.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Any, Iterable, List


# ---------------------------------------------------------------------------
# SHA checksum (Spec §6.7)
# ---------------------------------------------------------------------------


def compute_sha_checksum(sha_list: Iterable[str]) -> str:
    """Compute the SHA-256 checksum of a list of full commit SHAs.

    Formula (Spec §6.7)::

        sha256("\\n".join(sorted(full_sha_list)))

    The result is order-independent: the same set of SHAs always produces the
    same checksum regardless of the order they are supplied.

    Args:
        sha_list: Iterable of full 40-character commit SHA strings.

    Returns:
        Lowercase hex-encoded SHA-256 digest string.
    """
    joined = "\n".join(sorted(sha_list))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def write_jsonl(path: str, records: Iterable[Any]) -> None:
    """Write an iterable of JSON-serialisable objects to a JSONL file.

    Each object is written as a single line of JSON followed by a newline.
    The file is created or overwritten.  The caller is responsible for
    converting dataclasses/enums to plain dicts first (e.g. via
    :func:`changelog_tool.models.to_dict`).

    Args:
        path:    Destination file path.
        records: Iterable of JSON-serialisable objects (dicts, lists, scalars).

    Raises:
        OSError: If the file cannot be opened or written.
        TypeError: If a record is not JSON-serialisable.
    """
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")


def read_jsonl(path: str) -> List[Any]:
    """Read a JSONL file and return a list of parsed objects.

    Empty lines are skipped.

    Args:
        path: Source file path.

    Returns:
        List of parsed JSON objects (typically dicts).

    Raises:
        OSError: If the file cannot be opened.
        json.JSONDecodeError: If any non-empty line is not valid JSON.
    """
    results: List[Any] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                results.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise json.JSONDecodeError(
                    f"Invalid JSON on line {lineno} of {path}: {exc.msg}",
                    exc.doc,
                    exc.pos,
                ) from exc
    return results


# ---------------------------------------------------------------------------
# Atomic JSON write (crash-safe state files)
# ---------------------------------------------------------------------------


def write_json_atomic(path: str, data: Any, *, indent: int = 2) -> None:
    """Write *data* as JSON to *path* atomically.

    The data is first written to a temporary file in the same directory as
    *path*, then renamed into place via :func:`os.replace`.  On POSIX systems
    ``os.replace`` is atomic; on Windows it is atomic since Vista.  This
    guarantees that a crash mid-write never leaves a half-written file at
    *path*.

    Args:
        path:   Destination file path.
        data:   JSON-serialisable object.
        indent: Indentation level for pretty-printing (default 2).

    Raises:
        OSError:   If the directory does not exist or the rename fails.
        TypeError: If *data* is not JSON-serialisable.
    """
    dir_name = os.path.dirname(os.path.abspath(path))
    # Write to a temp file in the same directory so that os.replace is
    # guaranteed to be on the same filesystem (required for atomicity).
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=indent)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file if anything went wrong before the rename.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json(path: str) -> Any:
    """Read and parse a JSON file.

    Args:
        path: Source file path.

    Returns:
        Parsed JSON value (typically a dict).

    Raises:
        OSError:             If the file cannot be opened.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Workdir initialisation
# ---------------------------------------------------------------------------


def ensure_workdir(workdir: str) -> None:
    """Create the pipeline working directory and its ``logs/`` subdirectory.

    Idempotent — safe to call even if the directories already exist.

    Args:
        workdir: Path to the working directory (e.g. ``.changelog``).
    """
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.join(workdir, "logs"), exist_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "compute_sha_checksum",
    "write_jsonl",
    "read_jsonl",
    "write_json_atomic",
    "read_json",
    "ensure_workdir",
]
