from __future__ import annotations
import json, shutil
from pathlib import Path
import pytest
import validate_workflow_contracts as vwc
FIXTURES = Path(__file__).parent.parent / "fixtures" / "workflow-contracts"

def _run(fixture_name, tmp_path):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / fixture_name, wdir / fixture_name)
    out = tmp_path / "result.json"
    ec = vwc.run(tmp_path, out, "text", True)
    return ec, json.loads(out.read_text())

def test_valid_bootstrap_job_passes(tmp_path):
    ec, d = _run("valid-bootstrap-job.yml", tmp_path)
    assert ec == 0 and d["result"] == "passed"

def test_nonbootstrap_missing_strict_shell_is_observed_not_blocking(tmp_path):
    wf = (
        "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: multi\n        run: |\n"
        "          echo one\n          echo two\n          make release\n"
    )
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "app.yml").write_text(wf)
    out = tmp_path / "result.json"
    ec = vwc.run(tmp_path, out, "text", True)
    d = json.loads(out.read_text())
    assert ec == 0 and d["result"] == "passed"
    assert any(w["code"] == "STRICT_SHELL_OBSERVED" for w in d.get("warnings", []))

def test_pr_target_forbidden(tmp_path):
    ec, d = _run("invalid-pr-target.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "PR_TARGET_FORBIDDEN" for v in d["violations"])

def test_missing_permissions_fails(tmp_path):
    ec, d = _run("invalid-missing-permissions.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "BOOTSTRAP_PERMISSIONS_MISSING" for v in d["violations"])

def test_missing_timeout_fails(tmp_path):
    ec, d = _run("invalid-missing-timeout.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "BOOTSTRAP_TIMEOUT_MISSING" for v in d["violations"])

def test_unsafe_shell_fails(tmp_path):
    ec, d = _run("invalid-unsafe-shell.yml", tmp_path)
    assert ec == 1 and any(v["code"] == "BOOTSTRAP_SHELL_UNSAFE" for v in d["violations"])


# --- Frozen public interface (pr-pipeline.yml) ------------------------------
def _stage_pipeline(tmp_path, body, with_fixture=True):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "pr-pipeline.yml").write_text(body)
    if with_fixture:
        fdir = tmp_path / "tests" / "fixtures" / "workflow-contracts"
        fdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "pr-pipeline-public-interface.json",
                    fdir / "pr-pipeline-public-interface.json")
    out = tmp_path / "result.json"
    ec = vwc.run(tmp_path, out, "text", True)
    return ec, json.loads(out.read_text())


_FULL_IFACE = (
    "name: PR pipeline\n"
    "on:\n  workflow_call:\n    inputs:\n"
    '      python-version: {type: string, required: false, default: "3.12"}\n'
    '      source-dir: {type: string, required: false, default: "."}\n'
    '      test-dir: {type: string, required: false, default: "tests/"}\n'
    '      requirements-file: {type: string, required: false, default: "requirements-ci.txt"}\n'
    '      extra-install-command: {type: string, required: false, default: ""}\n'
    "      enable-pydantic-strict: {type: boolean, required: false, default: false}\n"
    '      l9-ci-install-command: {type: string, required: false, default: "pip install l9-ci"}\n'
    '      changed-files: {type: string, required: false, default: ""}\n'
    '      pr-labels: {type: string, required: false, default: ""}\n'
    "      labels-known: {type: boolean, required: false, default: true}\n"
    "jobs:\n  noop:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
)


def test_public_interface_preserved_passes(tmp_path):
    ec, d = _stage_pipeline(tmp_path, _FULL_IFACE)
    assert ec == 0 and d["result"] == "passed"


def test_public_interface_missing_workflow_call_fails(tmp_path):
    body = "name: pr\non:\n  pull_request:\njobs:\n  noop:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
    ec, d = _stage_pipeline(tmp_path, body)
    assert ec == 1 and any(v["code"] == "PUBLIC_INTERFACE_BREAK" for v in d["violations"])


def test_public_interface_dropped_input_fails(tmp_path):
    # Drop the last input to break the interface.
    body = _FULL_IFACE.replace(
        "      labels-known: {type: boolean, required: false, default: true}\n", "")
    ec, d = _stage_pipeline(tmp_path, body)
    assert ec == 1 and any(v["code"] == "PUBLIC_INTERFACE_BREAK" for v in d["violations"])


def test_public_interface_fixture_missing_fails(tmp_path):
    # CRITICAL-05: fixture absent while pr-pipeline.yml exists must FAIL, not
    # silently pass.
    ec, d = _stage_pipeline(tmp_path, _FULL_IFACE, with_fixture=False)
    assert ec == 1 and any(v["code"] == "PUBLIC_INTERFACE_FIXTURE_MISSING" for v in d["violations"])


# --- Contract debt registry -------------------------------------------------
def _run_with_debt(debt_fixture, tmp_path):
    # legacy.yml has a nontrivial run block lacking a strict-shell preamble in
    # a bootstrap job, which the debt entry (STRICT_SHELL_REQUIRED) may waive.
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "legacy.yml").write_text(
        "name: legacy\non: push\njobs:\n  legacy-bootstrap:\n"
        "    permissions:\n      contents: read\n    timeout-minutes: 10\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: Install legacy tooling\n        run: |\n"
        "            echo installing\n            pip download something\n"
    )
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / debt_fixture, gov / "workflow-contract-debt.yaml")
    out = tmp_path / "result.json"
    ec = vwc.run(tmp_path, out, "text", True)
    return ec, json.loads(out.read_text())


def test_expired_debt_rejected(tmp_path):
    ec, d = _run_with_debt("expired-contract-debt.yaml", tmp_path)
    assert ec == 1 and any(v["code"] == "DEBT_EXPIRED" for v in d["violations"])


def test_debt_schema_fails_closed(tmp_path, monkeypatch):
    import l9_bootstrap.schema_loader as sl
    def _boom(root, name):
        raise sl.SchemaUnavailable("missing")
    monkeypatch.setattr(sl, "load_validator", _boom)
    ec, d = _run_with_debt("valid-contract-debt.yaml", tmp_path)
    assert ec == 2 and any(v["code"] == "SCHEMA_UNAVAILABLE" for v in d["violations"])
