from __future__ import annotations
import pytest
from l9_bootstrap.yaml_loader import parse_yaml_string, load_yaml_file

def test_on_preserved_as_string():
    doc = parse_yaml_string("on: push\n")
    assert "on" in doc

def test_simple_mapping():
    doc = parse_yaml_string("a: 1\nb: 2\n")
    assert doc["a"] == 1 and doc["b"] == 2

def test_file_not_found_raises(tmp_path):
    with pytest.raises(ValueError): load_yaml_file(tmp_path / "missing.yml")

def test_file_too_large_raises(tmp_path):
    import l9_bootstrap.limits as lm
    orig = lm.MAX_WORKFLOW_FILE_BYTES
    lm.MAX_WORKFLOW_FILE_BYTES = 5
    f = tmp_path / "big.yml"
    f.write_text("on: push\njobs: {}\n")
    try:
        with pytest.raises(ValueError): load_yaml_file(f)
    finally:
        lm.MAX_WORKFLOW_FILE_BYTES = orig

def test_load_yaml_file_reads_file(tmp_path):
    f = tmp_path / "wf.yml"
    f.write_text("on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n")
    doc = load_yaml_file(f)
    assert "jobs" in doc
