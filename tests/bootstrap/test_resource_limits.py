from __future__ import annotations
import pytest
import l9_bootstrap.limits as lm
from l9_bootstrap.workflow_scan import iter_workflow_files

def test_limit_constants_positive():
    for name in ["MAX_WORKFLOW_FILES","MAX_WORKFLOW_FILE_BYTES","MAX_YAML_DEPTH",
                 "MAX_RUN_BLOCK_BYTES","MAX_REGISTRY_ENTRIES","MAX_RESULT_FILE_BYTES",
                 "MAX_TOTAL_SCAN_BYTES"]:
        assert getattr(lm, name) > 0

def test_too_many_workflow_files_raises(tmp_path):
    orig = lm.MAX_WORKFLOW_FILES
    lm.MAX_WORKFLOW_FILES = 2
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    for i in range(3):
        (wdir/f"wf{i}.yml").write_text("on: push\n")
    try:
        with pytest.raises(ValueError, match="Too many"):
            list(iter_workflow_files(wdir, tmp_path))
    finally:
        lm.MAX_WORKFLOW_FILES = orig

def test_symlinked_workflow_is_rejected(tmp_path):
    # A symlink under .github/workflows is an ambiguous execution surface. The
    # contract requires the validator to fail closed rather than silently skip.
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    real = tmp_path / "real.yml"
    real.write_text("on: push\n")
    (wdir/"sym.yml").symlink_to(real)
    with pytest.raises(ValueError, match="Symlinked workflow"):
        list(iter_workflow_files(wdir, tmp_path))
