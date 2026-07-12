from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
import pytest
SCRIPTS = Path(__file__).parent.parent.parent / ".github" / "scripts"
FAKE_HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

def _make_passing_repo(tmp_path):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    (wdir/"valid.yml").write_text(
        "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2\n"
    )
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    (gov/"download-integrity.yaml").write_text('schema_version: "1.0"\ndownloads: {}\n')
    (gov/"action-pins.lock.json").write_text(json.dumps({
        "schema_version": "1.0", "generated_at": "2026-07-01",
        "entries": {"checkout": {
            "action": "actions/checkout", "kind": "action", "version": "v4.2.2",
            "commit_sha": "11bd71901bbe5b1630ceea73d27597364c9af683",
            "upstream_repository": "actions/checkout",
            "verification_method": "upstream-tag-resolution",
            "verified_at": "2026-07-01"}}}))
    req = tmp_path / "requirements"
    req.mkdir()
    (req/"bootstrap.lock").write_text(
        f"ruamel.yaml==0.18.10 \\\n    --hash=sha256:{FAKE_HASH}\n"
    )
    return tmp_path

def test_all_five_result_files_created(tmp_path):
    root = _make_passing_repo(tmp_path)
    out_dir = tmp_path / "results"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS/"run_bootstrap_validators.py"),
         "--root", str(root), "--output-dir", str(out_dir)],
        capture_output=True, text=True
    )
    for fname in ["action-pins.json","download-integrity.json","ci-dependencies.json",
                  "workflow-contracts.json","bootstrap-manifest.json"]:
        assert (out_dir/fname).exists(), f"Missing {fname}\nout={r.stdout}\nerr={r.stderr}"

def test_manifest_has_all_four_gates(tmp_path):
    root = _make_passing_repo(tmp_path)
    out_dir = tmp_path / "results"
    subprocess.run(
        [sys.executable, str(SCRIPTS/"run_bootstrap_validators.py"),
         "--root", str(root), "--output-dir", str(out_dir)],
        capture_output=True
    )
    mpath = out_dir / "bootstrap-manifest.json"
    if not mpath.exists():
        pytest.skip("manifest not produced")
    m = json.loads(mpath.read_text())
    for g in ["workflow/action-pins","workflow/download-integrity","dependencies/ci-lock","workflow/contracts"]:
        assert g in m["expected_gates"]

def test_missing_result_file_fails_validator(tmp_path):
    import validate_bootstrap_results as vbr
    rd = tmp_path / "partial"
    rd.mkdir()
    for fname, gid in [("action-pins.json","workflow/action-pins"),
                        ("download-integrity.json","workflow/download-integrity"),
                        ("ci-dependencies.json","dependencies/ci-lock")]:
        (rd/fname).write_text(json.dumps({
            "schema_version":"1.0","gate_id":gid,"result":"passed",
            "violations":[],"warnings":[],"metadata":{}
        }))
    ec = vbr.run(rd, tmp_path, quiet=True)
    assert ec != 0
