"""Tests for changelog_tool.io — SHA checksum, JSONL, atomic write, workdir."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from changelog_tool.io import (
    compute_sha_checksum,
    ensure_workdir,
    read_json,
    read_jsonl,
    write_json_atomic,
    write_jsonl,
)


# ---------------------------------------------------------------------------
# compute_sha_checksum (Spec §6.7)
# ---------------------------------------------------------------------------


class TestComputeShaChecksum:
    def test_known_value(self):
        shas = ["aaa", "bbb"]
        expected = hashlib.sha256("aaa\nbbb".encode()).hexdigest()
        assert compute_sha_checksum(shas) == expected

    def test_order_independent(self):
        shas_a = ["111", "222", "333"]
        shas_b = ["333", "111", "222"]
        assert compute_sha_checksum(shas_a) == compute_sha_checksum(shas_b)

    def test_single_sha(self):
        sha = "a" * 40
        expected = hashlib.sha256(sha.encode()).hexdigest()
        assert compute_sha_checksum([sha]) == expected

    def test_empty_list(self):
        # Empty list → sha256 of empty string
        expected = hashlib.sha256(b"").hexdigest()
        assert compute_sha_checksum([]) == expected

    def test_returns_hex_string(self):
        result = compute_sha_checksum(["abc"])
        assert isinstance(result, str)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_generator_input(self):
        """Should accept any iterable, not just lists."""
        shas = ["x" * 40, "y" * 40]
        result_list = compute_sha_checksum(shas)
        result_gen = compute_sha_checksum(s for s in shas)
        assert result_list == result_gen


# ---------------------------------------------------------------------------
# write_jsonl / read_jsonl
# ---------------------------------------------------------------------------


class TestWriteReadJsonl:
    def test_round_trip_dicts(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        records = [{"a": 1}, {"b": "hello"}, {"c": None}]
        write_jsonl(path, records)
        restored = read_jsonl(path)
        assert restored == records

    def test_empty_iterable(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        write_jsonl(path, [])
        assert read_jsonl(path) == []

    def test_single_record(self, tmp_path):
        path = str(tmp_path / "single.jsonl")
        write_jsonl(path, [{"key": "value"}])
        assert read_jsonl(path) == [{"key": "value"}]

    def test_unicode_preserved(self, tmp_path):
        path = str(tmp_path / "unicode.jsonl")
        records = [{"text": "Привет мир 🌍"}]
        write_jsonl(path, records)
        restored = read_jsonl(path)
        assert restored[0]["text"] == "Привет мир 🌍"

    def test_each_record_on_own_line(self, tmp_path):
        path = str(tmp_path / "lines.jsonl")
        write_jsonl(path, [{"n": 1}, {"n": 2}])
        with open(path, "r", encoding="utf-8") as fh:
            lines = [l for l in fh.readlines() if l.strip()]
        assert len(lines) == 2

    def test_read_skips_empty_lines(self, tmp_path):
        path = str(tmp_path / "gaps.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}\n\n{"b": 2}\n')
        result = read_jsonl(path)
        assert result == [{"a": 1}, {"b": 2}]

    def test_read_invalid_json_raises(self, tmp_path):
        path = str(tmp_path / "bad.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
        with pytest.raises(json.JSONDecodeError):
            read_jsonl(path)

    def test_overwrite_existing_file(self, tmp_path):
        path = str(tmp_path / "overwrite.jsonl")
        write_jsonl(path, [{"v": 1}])
        write_jsonl(path, [{"v": 2}])
        assert read_jsonl(path) == [{"v": 2}]

    def test_nested_structures(self, tmp_path):
        path = str(tmp_path / "nested.jsonl")
        records = [{"list": [1, 2, 3], "nested": {"x": True}}]
        write_jsonl(path, records)
        assert read_jsonl(path) == records


# ---------------------------------------------------------------------------
# write_json_atomic / read_json
# ---------------------------------------------------------------------------


class TestWriteJsonAtomic:
    def test_file_is_created(self, tmp_path):
        path = str(tmp_path / "state.json")
        write_json_atomic(path, {"status": "done"})
        assert os.path.exists(path)

    def test_content_is_correct(self, tmp_path):
        path = str(tmp_path / "state.json")
        data = {"batches": {"b1": "done"}, "count": 42}
        write_json_atomic(path, data)
        with open(path, "r", encoding="utf-8") as fh:
            restored = json.load(fh)
        assert restored == data

    def test_overwrites_existing(self, tmp_path):
        path = str(tmp_path / "state.json")
        write_json_atomic(path, {"v": 1})
        write_json_atomic(path, {"v": 2})
        restored = read_json(path)
        assert restored["v"] == 2

    def test_no_temp_file_left_on_success(self, tmp_path):
        path = str(tmp_path / "state.json")
        write_json_atomic(path, {"ok": True})
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "state.json"

    def test_unicode_preserved(self, tmp_path):
        path = str(tmp_path / "unicode.json")
        data = {"msg": "Привет 🌍"}
        write_json_atomic(path, data)
        restored = read_json(path)
        assert restored["msg"] == "Привет 🌍"

    def test_pretty_printed(self, tmp_path):
        path = str(tmp_path / "pretty.json")
        write_json_atomic(path, {"a": 1}, indent=2)
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        # Pretty-printed JSON has newlines
        assert "\n" in content

    def test_ends_with_newline(self, tmp_path):
        path = str(tmp_path / "newline.json")
        write_json_atomic(path, {"x": 1})
        with open(path, "rb") as fh:
            content = fh.read()
        assert content.endswith(b"\n")


class TestReadJson:
    def test_reads_valid_json(self, tmp_path):
        path = str(tmp_path / "data.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"key": "value"}, fh)
        assert read_json(path) == {"key": "value"}

    def test_raises_on_invalid_json(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not json")
        with pytest.raises(json.JSONDecodeError):
            read_json(path)

    def test_raises_on_missing_file(self, tmp_path):
        path = str(tmp_path / "missing.json")
        with pytest.raises(OSError):
            read_json(path)


# ---------------------------------------------------------------------------
# ensure_workdir
# ---------------------------------------------------------------------------


class TestEnsureWorkdir:
    def test_creates_workdir(self, tmp_path):
        workdir = str(tmp_path / ".changelog")
        ensure_workdir(workdir)
        assert os.path.isdir(workdir)

    def test_creates_logs_subdir(self, tmp_path):
        workdir = str(tmp_path / ".changelog")
        ensure_workdir(workdir)
        assert os.path.isdir(os.path.join(workdir, "logs"))

    def test_idempotent(self, tmp_path):
        workdir = str(tmp_path / ".changelog")
        ensure_workdir(workdir)
        # Second call must not raise
        ensure_workdir(workdir)
        assert os.path.isdir(os.path.join(workdir, "logs"))

    def test_nested_path(self, tmp_path):
        workdir = str(tmp_path / "a" / "b" / ".changelog")
        ensure_workdir(workdir)
        assert os.path.isdir(os.path.join(workdir, "logs"))
