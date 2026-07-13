from __future__ import annotations
import pytest
from l9_bootstrap.paths import safe_resolve, repo_root

def test_normal_resolve(tmp_path):
    f = tmp_path / "sub" / "file.txt"
    f.parent.mkdir(); f.write_text("x")
    assert safe_resolve(tmp_path, "sub/file.txt") == f.resolve()

def test_traversal_rejected(tmp_path):
    with pytest.raises(ValueError, match="Path escape"):
        safe_resolve(tmp_path, "../outside")

def test_deep_traversal_rejected(tmp_path):
    with pytest.raises(ValueError, match="Path escape"):
        safe_resolve(tmp_path, "a/../../outside")

def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path.parent / "outside_safe_test"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink escape"):
        safe_resolve(tmp_path, "link")

def test_repo_root_returns_path():
    assert repo_root().exists()
