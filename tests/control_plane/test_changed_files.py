"""PR-B2: changed-file acquisition (PR §8)."""
from __future__ import annotations

import subprocess

import pytest

from l9_ci_core.control_plane import changed_files, schemas


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / "keep.txt").write_text("k\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    (tmp_path / "a.txt").write_text("two\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("d\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "subject")
    subject = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return tmp_path, base, subject


def test_collect_returns_sorted_unique_posix_paths(repo):
    root, base, subject = repo
    result = changed_files.collect(base, subject, root=root)
    schemas.validate("changed-files", result.to_dict())
    assert result.unknown_diff is False
    assert result.reason is None
    assert result.files == ["a.txt", "docs/x.md"]
    assert result.files == sorted(result.files)


def test_missing_base_is_unknown_diff(repo):
    root, _base, subject = repo
    result = changed_files.collect(None, subject, root=root)
    schemas.validate("changed-files", result.to_dict())
    assert result.unknown_diff is True
    assert result.reason == "missing_base_sha"
    assert result.files == []


def test_diff_failure_is_unknown_diff_not_raise(repo):
    root, _base, _subject = repo
    result = changed_files.collect("d" * 40, "e" * 40, root=root)
    schemas.validate("changed-files", result.to_dict())
    assert result.unknown_diff is True
    assert result.reason == "git_diff_failed"


def test_diff_failure_when_not_a_repo(tmp_path):
    result = changed_files.collect("a" * 40, "b" * 40, root=tmp_path)
    assert result.unknown_diff is True
    assert result.reason == "git_diff_failed"
