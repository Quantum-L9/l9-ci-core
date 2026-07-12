from __future__ import annotations
import json, shutil
from pathlib import Path
import pytest
import validate_ci_dependencies as vcd
FIXTURES = Path(__file__).parent.parent / "fixtures" / "ci-dependencies"
FAKE_HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
GOOD_LOCK = f"ruamel.yaml==0.18.10 \\\n    --hash=sha256:{FAKE_HASH}\n"

def _run(fixture, tmp_path, lock=GOOD_LOCK):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / fixture, wdir / fixture)
    req = tmp_path / "requirements"
    req.mkdir(exist_ok=True)
    (req / "bootstrap.lock").write_text(lock)
    out = tmp_path / "result.json"
    ec = vcd.run(tmp_path, out, "text", True)
    return ec, json.loads(out.read_text())

def test_require_hashes_passes(tmp_path):
    ec, d = _run("valid-require-hashes.yml", tmp_path)
    assert ec == 0 and d["result"] == "passed"

def test_local_editable_passes(tmp_path):
    ec, d = _run("valid-local-editable.yml", tmp_path)
    assert ec == 0

def test_unbounded_install_fails(tmp_path):
    ec, d = _run("invalid-unbounded-install.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "UNBOUNDED_PIP_INSTALL" for v in d["violations"])

def test_upgrade_pip_fails(tmp_path):
    ec, d = _run("invalid-upgrade-install.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "UNCONDITIONAL_PIP_UPGRADE" for v in d["violations"])

def test_branch_url_fails(tmp_path):
    ec, d = _run("invalid-branch-url.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "BRANCH_URL_INSTALL" for v in d["violations"])

def test_lock_missing_hash_fails(tmp_path):
    ec, d = _run("valid-require-hashes.yml", tmp_path, lock="ruamel.yaml==0.18.10\n")
    assert ec == 1 and any(v["code"] == "LOCK_MISSING_HASH" for v in d["violations"])

def test_lock_forbidden_index_url_fails(tmp_path):
    bad = f"--index-url https://internal.example/simple\n{GOOD_LOCK}"
    ec, d = _run("valid-require-hashes.yml", tmp_path, lock=bad)
    assert ec == 1 and any(v["code"] == "LOCK_FORBIDDEN_OPTION" for v in d["violations"])

def test_lock_forbidden_find_links_fails(tmp_path):
    bad = f"--find-links ./wheels\n{GOOD_LOCK}"
    ec, d = _run("valid-require-hashes.yml", tmp_path, lock=bad)
    assert ec == 1 and any(v["code"] == "LOCK_FORBIDDEN_OPTION" for v in d["violations"])

def test_no_requirements_dir_is_error(tmp_path):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "result.json"
    ec = vcd.run(tmp_path, out, "text", True)
    assert ec == 2


# --- Exception registry (schema-validated, time-bounded) --------------------
_EXC_FIXTURES = Path(__file__).parent.parent / "fixtures" / "ci-dependencies"


def _run_with_exceptions(exc_fixture, tmp_path):
    """Run the gate with a governance exception registry staged, and a workflow
    that performs an unpinned pip install (UNPINNED_PIP_INSTALL) in job
    ``validate`` step ``install-legacy-tool`` so the fixture's line_or_step
    (``validate/install-legacy-tool``) can waive it."""
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "example.yml").write_text(
        "on: push\njobs:\n  validate:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: install-legacy-tool\n        run: pip install -r requirements/legacy.txt\n"
    )
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    shutil.copy(_EXC_FIXTURES / exc_fixture, gov / "ci-dependency-exceptions.yaml")
    req = tmp_path / "requirements"
    req.mkdir(exist_ok=True)
    (req / "bootstrap.lock").write_text(GOOD_LOCK)
    out = tmp_path / "result.json"
    ec = vcd.run(tmp_path, out, "text", True)
    return ec, json.loads(out.read_text())


def test_valid_exception_waives_finding(tmp_path):
    # A well-formed, in-window exception waives the UNPINNED_PIP_INSTALL finding
    # for the exact path + job/step it names.
    ec, d = _run_with_exceptions("valid-explicit-exception.yaml", tmp_path)
    assert ec == 0 and d["result"] == "passed"
    assert not any(v["code"] == "UNPINNED_PIP_INSTALL" for v in d["violations"])


def test_expired_exception_is_rejected(tmp_path):
    ec, d = _run_with_exceptions("expired-exception.yaml", tmp_path)
    assert ec == 1 and any(v["code"] == "EXCEPTION_EXPIRED" for v in d["violations"])


def test_missing_owner_exception_is_schema_invalid(tmp_path):
    # owner is required by the schema; its absence fails closed (error/exit 2).
    ec, d = _run_with_exceptions("missing-owner-exception.yaml", tmp_path)
    assert ec == 2 and d["result"] == "error"
    assert any(v["code"] == "EXCEPTION_SCHEMA_INVALID" for v in d["violations"])


def test_wildcard_exception_is_schema_invalid(tmp_path):
    # The path pattern forbids wildcards; a wildcard entry fails schema check.
    ec, d = _run_with_exceptions("wildcard-exception.yaml", tmp_path)
    assert ec == 2 and d["result"] == "error"
    assert any(v["code"] == "EXCEPTION_SCHEMA_INVALID" for v in d["violations"])


def test_over_window_exception_rejected(tmp_path):
    # Window is measured created_at..expires_on. A 60-day span exceeds 30 days
    # even though it is not yet expired.
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "example.yml").write_text(
        "on: push\njobs:\n  validate:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: install-legacy-tool\n        run: pip install -r requirements/legacy.txt\n"
    )
    from datetime import date, timedelta
    created = date.today()
    expires = created + timedelta(days=60)
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "ci-dependency-exceptions.yaml").write_text(
        'schema_version: "1.0"\nexceptions:\n'
        '  - path: ".github/workflows/example.yml"\n'
        '    line_or_step: "validate/install-legacy-tool"\n'
        '    violation_code: "UNPINNED_PIP_INSTALL"\n'
        '    reason: "Over-window exception used to prove the created..expires cap."\n'
        '    owner: "@quantum-l9/platform"\n'
        f'    created_at: "{created.isoformat()}"\n'
        f'    expires_on: "{expires.isoformat()}"\n'
        '    tracking_issue: "Quantum-L9/l9-ci-core#104"\n'
    )
    req = tmp_path / "requirements"
    req.mkdir(exist_ok=True)
    (req / "bootstrap.lock").write_text(GOOD_LOCK)
    out = tmp_path / "result.json"
    ec = vcd.run(tmp_path, out, "text", True)
    d = json.loads(out.read_text())
    assert ec == 1 and any(v["code"] == "EXCEPTION_WINDOW_TOO_LONG" for v in d["violations"])


def test_missing_jsonschema_or_schema_fails_closed(tmp_path, monkeypatch):
    # If the schema file is absent the exception registry cannot be trusted:
    # the gate must fail closed with SCHEMA_UNAVAILABLE (error/exit 2).
    import l9_bootstrap.schema_loader as sl
    def _boom(root, name):
        raise sl.SchemaUnavailable(f"schema {name} not found under {root}")
    monkeypatch.setattr(sl, "load_validator", _boom)
    ec, d = _run_with_exceptions("valid-explicit-exception.yaml", tmp_path)
    assert ec == 2 and any(v["code"] == "SCHEMA_UNAVAILABLE" for v in d["violations"])


# --- Phased-enforcement baseline tests (PR-A) -----------------------------
import hashlib as _hashlib

_UNBOUNDED_WF = """on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Install ruff
        run: |
          pip install ruff
"""


def _mk_baseline(entries):
    canon = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    dig = "sha256:" + _hashlib.sha256(canon.encode()).hexdigest()
    return {"schema_version": "1.0", "phase": "PR-A",
            "baseline_digest": dig, "entries": entries}


def _run_wf(tmp_path, wf_text, wf_name="w.yml", baseline=None, lock=GOOD_LOCK):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / wf_name).write_text(wf_text)
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (gov / "ci-dependency-baseline.json").write_text(json.dumps(baseline))
    req = tmp_path / "requirements"; req.mkdir(exist_ok=True)
    (req / "bootstrap.lock").write_text(lock)
    out = tmp_path / "result.json"
    ec = vcd.run(tmp_path, out, "text", True)
    return ec, json.loads(out.read_text())


def _cmd_sha(wf_text, wf_name, tmp_path):
    """Compute the validator's normalized command hash for the single install."""
    from l9_bootstrap.workflow_scan import iter_run_blocks
    from l9_bootstrap.yaml_loader import load_yaml_file
    p = tmp_path / "_probe.yml"; p.write_text(wf_text)
    wf = load_yaml_file(p)
    for jid, idx, name, rb, ln in iter_run_blocks(wf):
        if vcd._PIP_INSTALL_RE.search(rb):
            return vcd._command_sha256(rb)
    raise AssertionError("no install found")


def test_baselined_legacy_is_observation_not_blocking(tmp_path):
    sha = _cmd_sha(_UNBOUNDED_WF, "w.yml", tmp_path)
    bl = _mk_baseline([{"path": ".github/workflows/w.yml", "job": "build",
                        "step": "Install ruff", "violation_code": "UNBOUNDED_PIP_INSTALL",
                        "command_sha256": sha, "status": "legacy_observation"}])
    ec, d = _run_wf(tmp_path, _UNBOUNDED_WF, baseline=bl)
    assert ec == 0 and d["result"] == "passed"
    assert d["metadata"]["legacy_observation_count"] == 1
    assert d["metadata"]["new_violation_count"] == 0
    assert d["metadata"]["baseline_digest"] == bl["baseline_digest"]
    assert any(w["code"] == "UNBOUNDED_PIP_INSTALL_LEGACY_OBSERVED" for w in d["warnings"])


def test_new_install_not_in_baseline_blocks(tmp_path):
    # Empty baseline -> the live finding is brand-new -> blocking.
    bl = _mk_baseline([])
    ec, d = _run_wf(tmp_path, _UNBOUNDED_WF, baseline=bl)
    assert ec == 1 and d["result"] == "failed"
    assert any(v["code"] == "UNBOUNDED_PIP_INSTALL" for v in d["violations"])
    assert d["metadata"]["new_violation_count"] == 1


def test_changed_command_breaks_baseline_match(tmp_path):
    # Baseline records a different command hash for the same identity.
    bl = _mk_baseline([{"path": ".github/workflows/w.yml", "job": "build",
                        "step": "Install ruff", "violation_code": "UNBOUNDED_PIP_INSTALL",
                        "command_sha256": "0" * 64, "status": "legacy_observation"}])
    ec, d = _run_wf(tmp_path, _UNBOUNDED_WF, baseline=bl)
    assert ec == 1 and d["result"] == "failed"
    assert any(v["code"] == "UNBOUNDED_PIP_INSTALL_BASELINE_CHANGED" for v in d["violations"])


def test_tampered_digest_is_error(tmp_path):
    sha = _cmd_sha(_UNBOUNDED_WF, "w.yml", tmp_path)
    bl = _mk_baseline([{"path": ".github/workflows/w.yml", "job": "build",
                        "step": "Install ruff", "violation_code": "UNBOUNDED_PIP_INSTALL",
                        "command_sha256": sha, "status": "legacy_observation"}])
    bl["baseline_digest"] = "sha256:" + "f" * 64  # tamper
    ec, d = _run_wf(tmp_path, _UNBOUNDED_WF, baseline=bl)
    assert ec == 2 and d["result"] == "error"
    assert any(v["code"] == "BASELINE_DIGEST_MISMATCH" for v in d["violations"])


def test_stale_baseline_entry_blocks(tmp_path):
    # Baseline references a finding that does not exist in the workflow.
    bl = _mk_baseline([{"path": ".github/workflows/w.yml", "job": "ghost",
                        "step": "Nonexistent", "violation_code": "UNBOUNDED_PIP_INSTALL",
                        "command_sha256": "1" * 64, "status": "legacy_observation"}])
    # Use a require-hashes workflow so there is no live finding at all.
    clean_wf = "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pip install --require-hashes -r requirements/bootstrap.lock\n"
    ec, d = _run_wf(tmp_path, clean_wf, baseline=bl)
    assert ec == 1 and d["result"] == "failed"
    assert any(v["code"] == "BASELINE_ENTRY_STALE" for v in d["violations"])


def test_wildcard_baseline_entry_is_error(tmp_path):
    bl = _mk_baseline([{"path": ".github/workflows/w.yml", "job": "*",
                        "step": "Install ruff", "violation_code": "UNBOUNDED_PIP_INSTALL",
                        "command_sha256": "2" * 64, "status": "legacy_observation"}])
    # digest must be recomputed over the wildcard entry to reach the wildcard check
    ec, d = _run_wf(tmp_path, _UNBOUNDED_WF, baseline=bl)
    assert ec == 2 and d["result"] == "error"
    assert any(v["code"] in ("BASELINE_WILDCARD_FORBIDDEN", "BASELINE_SCHEMA_INVALID") for v in d["violations"])


def test_bootstrap_managed_install_never_baselined(tmp_path):
    # An install that references the bootstrap lock is bootstrap-managed and must
    # comply even if someone tries to baseline it. Here it lacks --require-hashes.
    wf = ("on: push\njobs:\n  bootstrap:\n    runs-on: ubuntu-latest\n"
          "    steps:\n      - name: Install bootstrap\n        run: |\n"
          "          pip install -r requirements/bootstrap.lock\n")
    sha = _cmd_sha(wf, "w.yml", tmp_path)
    bl = _mk_baseline([{"path": ".github/workflows/w.yml", "job": "bootstrap",
                        "step": "Install bootstrap", "violation_code": "UNPINNED_PIP_INSTALL",
                        "command_sha256": sha, "status": "legacy_observation"}])
    ec, d = _run_wf(tmp_path, wf, baseline=bl)
    # bootstrap-managed -> blocking violation (not observed), AND the baseline
    # entry becomes stale because it was never matched.
    assert ec == 1 and d["result"] == "failed"
    assert any(v["code"] == "UNPINNED_PIP_INSTALL" for v in d["violations"])
