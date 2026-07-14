from __future__ import annotations
import json
from l9_bootstrap.output import write_json, _serialize


def test_creates_file(tmp_path):
    dest = tmp_path / "out.json"
    write_json({"a": 1}, dest)
    assert dest.exists() and json.loads(dest.read_text())["a"] == 1


def test_deterministic():
    d = {"z": 3, "a": 1, "m": 2}
    assert _serialize(d) == _serialize(d)


def test_sorted_keys(tmp_path):
    dest = tmp_path / "out.json"
    write_json({"z": 3, "a": 1}, dest)
    text = dest.read_text()
    assert text.index('"a"') < text.index('"z"')


def test_ends_with_newline(tmp_path):
    dest = tmp_path / "out.json"
    write_json({}, dest)
    assert dest.read_bytes().endswith(b"\n")
