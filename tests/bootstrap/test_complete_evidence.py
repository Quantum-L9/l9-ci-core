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
    # The manifest is a required artifact of a completed run; its absence is a
    # hard failure, never a skip.
    assert mpath.exists(), f"manifest not produced\nout={out_dir}"
    m = json.loads(mpath.read_text())
    for g in ["workflow/action-pins","workflow/download-integrity","dependencies/ci-lock","workflow/contracts"]:
        assert g in m["expected_gates"]


_ALL_FILES = [
    ("action-pins.json", "workflow/action-pins"),
    ("download-integrity.json", "workflow/download-integrity"),
    ("ci-dependencies.json", "dependencies/ci-lock"),
    ("workflow-contracts.json", "workflow/contracts"),
]
_RESULT_TO_EXIT = {"passed": 0, "failed": 1, "error": 2}


def _write_results(rd, results):
    """Write the four per-gate result files. ``results`` maps filename->result."""
    for fname, gid in _ALL_FILES:
        rv = results[fname]
        (rd / fname).write_text(json.dumps({
            "schema_version": "1.0", "gate_id": gid, "result": rv,
            "violations": [] if rv == "passed" else [{"code": "X", "message": "m"}],
            "warnings": [], "metadata": {},
        }))


def _manifest(results, complete, overall):
    return {
        "schema_version": "1.0",
        "expected_gates": [g for _, g in _ALL_FILES],
        "results": [
            {"gate_id": gid, "file": fname, "result": results[fname],
             "exit_code": _RESULT_TO_EXIT[results[fname]]}
            for fname, gid in _ALL_FILES
        ],
        "complete": complete,
        "overall_result": overall,
    }


def test_manifest_result_mismatch_is_rejected(tmp_path):
    """A schema-valid manifest whose per-gate claim disagrees with the actual
    result evidence file must be rejected (MANIFEST_RESULT_MISMATCH)."""
    import validate_bootstrap_results as vbr
    rd = tmp_path / "results"
    rd.mkdir()
    actual = {f: "passed" for f, _ in _ALL_FILES}
    _write_results(rd, actual)
    # Manifest lies: claims download-integrity failed though the evidence passed.
    lying = dict(actual)
    lying["download-integrity.json"] = "failed"
    manifest = _manifest(lying, complete=True, overall="failed")
    (rd / "bootstrap-manifest.json").write_text(json.dumps(manifest))
    ec = vbr.run(rd, tmp_path, quiet=True)
    assert ec != 0


def test_manifest_overall_mismatch_is_rejected(tmp_path):
    """A manifest that agrees per-gate but records the wrong overall_result is
    rejected (MANIFEST_OVERALL_MISMATCH)."""
    import validate_bootstrap_results as vbr
    rd = tmp_path / "results"
    rd.mkdir()
    actual = {f: "passed" for f, _ in _ALL_FILES}
    _write_results(rd, actual)
    # All gates passed, but the manifest claims overall failed.
    manifest = _manifest(actual, complete=True, overall="failed")
    (rd / "bootstrap-manifest.json").write_text(json.dumps(manifest))
    ec = vbr.run(rd, tmp_path, quiet=True)
    assert ec != 0


def test_manifest_matching_actual_passes(tmp_path):
    """Sanity: a truthful manifest over four passing gates validates cleanly."""
    import validate_bootstrap_results as vbr
    rd = tmp_path / "results"
    rd.mkdir()
    actual = {f: "passed" for f, _ in _ALL_FILES}
    _write_results(rd, actual)
    manifest = _manifest(actual, complete=True, overall="passed")
    (rd / "bootstrap-manifest.json").write_text(json.dumps(manifest))
    ec = vbr.run(rd, tmp_path, quiet=True)
    assert ec == 0

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
