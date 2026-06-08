"""Tests for changelog_tool.git — git subprocess helpers.

These tests use a real temporary git repository created with subprocess calls
so they exercise the actual git integration without mocking.  They require
``git`` to be installed on the test machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap

import pytest

from changelog_tool.git import (
    GitError,
    _normalise_filename,
    _parse_co_authors,
    _split_message,
    collect_commits,
    utc_now_iso,
)
from changelog_tool.models import CoAuthor, SizeBucket


# ---------------------------------------------------------------------------
# Fixtures: temporary git repo
# ---------------------------------------------------------------------------


def _git(args: list, cwd: str) -> str:
    """Run a git command and return stdout. Raises on failure."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"git {args} failed:\n{result.stderr}"
    return result.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """Create a minimal git repository with a few commits and return its path."""
    r = str(tmp_path / "repo")
    os.makedirs(r)

    _git(["init", "-b", "main"], r)
    _git(["config", "user.email", "test@example.com"], r)
    _git(["config", "user.name", "Test User"], r)

    # Commit 1: initial
    (tmp_path / "repo" / "README.md").write_text("# Hello\n")
    _git(["add", "README.md"], r)
    _git(["commit", "-m", "Initial commit"], r)

    # Tag v0 at this point (will be used as from_ref)
    _git(["tag", "v0"], r)

    # Commit 2: add a file
    (tmp_path / "repo" / "src.py").write_text("x = 1\ny = 2\n")
    _git(["add", "src.py"], r)
    _git(["commit", "-m", "Add src.py\n\nSome body text."], r)

    # Commit 3: modify file
    (tmp_path / "repo" / "src.py").write_text("x = 1\ny = 2\nz = 3\n")
    _git(["add", "src.py"], r)
    _git(
        [
            "commit",
            "-m",
            "Update src.py\n\nCo-authored-by: Alice <alice@example.com>\nCo-authored-by: Bob <bob@example.com>",
        ],
        r,
    )

    return r


@pytest.fixture()
def repo_with_merge(tmp_path):
    """Create a repo with a merge commit."""
    r = str(tmp_path / "repo")
    os.makedirs(r)

    _git(["init", "-b", "main"], r)
    _git(["config", "user.email", "test@example.com"], r)
    _git(["config", "user.name", "Test User"], r)

    # Base commit
    (tmp_path / "repo" / "a.txt").write_text("a\n")
    _git(["add", "a.txt"], r)
    _git(["commit", "-m", "Base"], r)
    _git(["tag", "v0"], r)

    # Branch off
    _git(["checkout", "-b", "feature"], r)
    (tmp_path / "repo" / "b.txt").write_text("b\n")
    _git(["add", "b.txt"], r)
    _git(["commit", "-m", "Feature commit"], r)

    # Back to main, add another commit
    _git(["checkout", "main"], r)
    (tmp_path / "repo" / "c.txt").write_text("c\n")
    _git(["add", "c.txt"], r)
    _git(["commit", "-m", "Main commit"], r)

    # Merge
    _git(["merge", "--no-ff", "feature", "-m", "Merge feature into main"], r)

    return r


# ---------------------------------------------------------------------------
# collect_commits — basic
# ---------------------------------------------------------------------------


class TestCollectCommits:
    def test_returns_commits_in_range(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        assert len(commits) == 2  # commits 2 and 3

    def test_sha_fields(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert len(c.sha) == 40
            assert len(c.short_sha) >= 4
            assert c.sha.startswith(c.short_sha)

    def test_commit_url(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert c.commit_url == f"https://github.com/org/repo/commit/{c.sha}"

    def test_author_fields(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert c.author_name == "Test User"
            assert c.author_email == "test@example.com"

    def test_subject_and_body(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        # Newest first — commit 3 is first
        c3 = commits[0]
        assert c3.subject == "Update src.py"
        assert "Co-authored-by" in c3.body

        c2 = commits[1]
        assert c2.subject == "Add src.py"
        assert c2.body == "Some body text."

    def test_co_authors_parsed(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        c3 = commits[0]  # has co-authors
        assert len(c3.co_authors) == 2
        emails = {ca.email for ca in c3.co_authors}
        assert "alice@example.com" in emails
        assert "bob@example.com" in emails

    def test_no_co_authors_on_plain_commit(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        c2 = commits[1]
        assert c2.co_authors == []

    def test_size_score_and_bucket(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert c.size_score == c.insertions + c.deletions
            assert c.size_bucket is not None

    def test_changed_files_populated(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert len(c.changed_files) > 0
            assert c.files_count == len(c.changed_files)

    def test_is_merge_commit_false_for_regular(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert c.is_merge_commit is False

    def test_github_fields_are_none(self, repo):
        """Stage-2 fields must be None after stage 1."""
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert c.github_login is None
            assert c.github_profile_url is None
            assert c.is_external is None

    def test_classification_fields_are_none(self, repo):
        """Stage-3/4 fields must be None after stage 1."""
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        for c in commits:
            assert c.auto_classification is None
            assert c.llm_classification is None

    def test_empty_range_returns_empty_list(self, repo):
        # HEAD..HEAD is empty
        commits = collect_commits(repo, "HEAD", "HEAD", "https://github.com/org/repo")
        assert commits == []

    def test_sha_uniqueness(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        shas = [c.sha for c in commits]
        assert len(shas) == len(set(shas))

    def test_github_url_trailing_slash_stripped(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo/")
        for c in commits:
            assert "//" not in c.commit_url.replace("https://", "")


class TestCollectCommitsMerge:
    def test_merge_commit_detected(self, repo_with_merge):
        commits = collect_commits(
            repo_with_merge, "v0", "HEAD", "https://github.com/org/repo"
        )
        merge_commits = [c for c in commits if c.is_merge_commit]
        assert len(merge_commits) >= 1
        assert merge_commits[0].subject.startswith("Merge")

    def test_merge_commits_included(self, repo_with_merge):
        """Merge commits must NOT be excluded (no --no-merges)."""
        commits = collect_commits(
            repo_with_merge, "v0", "HEAD", "https://github.com/org/repo"
        )
        subjects = [c.subject for c in commits]
        assert any("Merge" in s for s in subjects)


class TestCollectCommitsErrors:
    def test_invalid_repo_path(self, tmp_path):
        with pytest.raises(GitError):
            collect_commits(
                str(tmp_path / "nonexistent"),
                "v0",
                "HEAD",
                "https://github.com/org/repo",
            )

    def test_not_a_git_repo(self, tmp_path):
        plain_dir = str(tmp_path / "plain")
        os.makedirs(plain_dir)
        with pytest.raises(GitError):
            collect_commits(plain_dir, "v0", "HEAD", "https://github.com/org/repo")

    def test_invalid_ref(self, repo):
        with pytest.raises(GitError):
            collect_commits(
                repo, "nonexistent-ref-xyz", "HEAD", "https://github.com/org/repo"
            )


# ---------------------------------------------------------------------------
# _split_message
# ---------------------------------------------------------------------------


class TestSplitMessage:
    def test_subject_only(self):
        subject, body = _split_message("Fix bug\n")
        assert subject == "Fix bug"
        assert body == ""

    def test_subject_and_body(self):
        subject, body = _split_message("Fix bug\n\nDetailed explanation.\n")
        assert subject == "Fix bug"
        assert body == "Detailed explanation."

    def test_leading_blank_lines_ignored(self):
        subject, body = _split_message("\n\nFix bug\n\nBody.\n")
        assert subject == "Fix bug"
        assert body == "Body."

    def test_multiline_body(self):
        msg = "Add feature\n\nLine 1.\nLine 2.\nLine 3.\n"
        subject, body = _split_message(msg)
        assert subject == "Add feature"
        assert "Line 1." in body
        assert "Line 3." in body

    def test_empty_message(self):
        subject, body = _split_message("")
        assert subject == ""
        assert body == ""


# ---------------------------------------------------------------------------
# _parse_co_authors
# ---------------------------------------------------------------------------


class TestParseCoAuthors:
    def test_single_co_author(self):
        body = "Some body.\n\nCo-authored-by: Alice <alice@example.com>"
        result = _parse_co_authors(body)
        assert len(result) == 1
        assert result[0].name == "Alice"
        assert result[0].email == "alice@example.com"

    def test_multiple_co_authors(self):
        body = (
            "Body.\n\n"
            "Co-authored-by: Alice <alice@example.com>\n"
            "Co-authored-by: Bob <bob@example.com>"
        )
        result = _parse_co_authors(body)
        assert len(result) == 2
        names = {ca.name for ca in result}
        assert "Alice" in names
        assert "Bob" in names

    def test_no_co_authors(self):
        assert _parse_co_authors("Just a body.") == []

    def test_case_insensitive(self):
        body = "co-authored-by: Carol <carol@example.com>"
        result = _parse_co_authors(body)
        assert len(result) == 1
        assert result[0].name == "Carol"

    def test_github_fields_are_none(self):
        body = "Co-authored-by: Dave <dave@example.com>"
        result = _parse_co_authors(body)
        assert result[0].github_login is None
        assert result[0].github_profile_url is None
        assert result[0].is_external is None

    def test_name_with_spaces(self):
        body = "Co-authored-by: John Doe <john.doe@example.com>"
        result = _parse_co_authors(body)
        assert result[0].name == "John Doe"


# ---------------------------------------------------------------------------
# _normalise_filename
# ---------------------------------------------------------------------------


class TestNormaliseFilename:
    def test_plain_filename(self):
        assert _normalise_filename("src/foo.cpp") == "src/foo.cpp"

    def test_brace_rename(self):
        # git rename: "src/{old => new}.cpp"
        result = _normalise_filename("src/{old => new}.cpp")
        assert result == "src/new.cpp"

    def test_arrow_rename(self):
        result = _normalise_filename("old/path.cpp => new/path.cpp")
        assert result == "new/path.cpp"

    def test_no_double_slash(self):
        result = _normalise_filename("{old => new}/file.cpp")
        assert "//" not in result


# ---------------------------------------------------------------------------
# utc_now_iso
# ---------------------------------------------------------------------------


class TestUtcNowIso:
    def test_format(self):
        ts = utc_now_iso()
        # Should match YYYY-MM-DDTHH:MM:SSZ
        import re
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts)

    def test_ends_with_z(self):
        assert utc_now_iso().endswith("Z")


# ---------------------------------------------------------------------------
# Stage 1 integration: collect + write artifacts
# ---------------------------------------------------------------------------


class TestStage1Integration:
    """End-to-end test: collect_commits → write JSONL + manifest."""

    def test_jsonl_round_trip(self, repo, tmp_path):
        from changelog_tool.io import read_jsonl, write_jsonl
        from changelog_tool.models import commit_from_dict

        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        path = str(tmp_path / "commits.jsonl")
        write_jsonl(path, [__import__("changelog_tool.models", fromlist=["to_dict"]).to_dict(c) for c in commits])

        restored = [commit_from_dict(d) for d in read_jsonl(path)]
        assert len(restored) == len(commits)
        for orig, rest in zip(commits, restored):
            assert orig.sha == rest.sha
            assert orig.subject == rest.subject
            assert orig.size_score == rest.size_score
            assert orig.size_bucket == rest.size_bucket

    def test_manifest_checksum_matches(self, repo):
        from changelog_tool.io import compute_sha_checksum

        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        sha_list = [c.sha for c in commits]
        checksum = compute_sha_checksum(sha_list)
        # Recompute with different order — must match
        import random
        shuffled = sha_list[:]
        random.shuffle(shuffled)
        assert compute_sha_checksum(shuffled) == checksum

    def test_total_matches_sha_list(self, repo):
        commits = collect_commits(repo, "v0", "HEAD", "https://github.com/org/repo")
        assert len(commits) == 2
