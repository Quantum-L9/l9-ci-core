from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / ".github" / "scripts" / "classify_pr.py"


def load_workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def workflow_call_inputs(data: dict) -> dict:
    on_block = data.get(True) or data.get("on") or {}
    return on_block.get("workflow_call", {}).get("inputs", {})


def test_all_workflows_parse_as_yaml() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), path
        assert "jobs" in data, path


def test_new_workflows_use_v6_actions() -> None:
    for name in ["nightly.yml", "pre-commit-ci.yml", "release-publish.yml"]:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "actions/checkout@v6" in text
        assert "actions/setup-python@v6" in text
        assert "actions/checkout@v4" not in text
        assert "actions/setup-python@v5" not in text


def test_security_declares_l9_install_input() -> None:
    inputs = workflow_call_inputs(load_workflow("security.yml"))
    assert "l9-ci-install-command" in inputs


def test_aggregator_jobs_exist() -> None:
    expected = {
        "nightly.yml": "all-gates-passed",
        "pre-commit-ci.yml": "pre-commit-passed",
        "release-publish.yml": "all-release-gates-passed",
    }
    for name, job in expected.items():
        assert job in load_workflow(name)["jobs"]


def test_no_hardcoded_gate_sdk_paths_in_new_workflows() -> None:
    forbidden = [
        "contracts/transport-packet.schema.json",
        "scripts/generate_schema.py",
        "scripts/validate_contracts.py",
    ]
    for name in ["nightly.yml", "pre-commit-ci.yml", "release-publish.yml"]:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for item in forbidden:
            assert item not in text


def test_trio_governance_declares_distinct_tokens() -> None:
    text = (WORKFLOWS / "trio-governance.yml").read_text(encoding="utf-8")
    assert "audit-token-secret-name" in text
    assert "implementer-token-secret-name" in text
    assert "validator-token-secret-name" in text
    assert "l9-ci-audit-marker: v1" in text


def test_classifier_cli_plain_outputs_canonical_class() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--plain", "docs/readme.md"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "docs_only"


def test_classifier_cli_json_output() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), ".github/governance/audit-policy.yml"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["pr_class"] == "compliance"
    assert payload["changed_files"] == [".github/governance/audit-policy.yml"]


def test_classifier_env_input(monkeypatch) -> None:
    monkeypatch.setenv("CHANGED_FILES", "README.md,docs/a.md")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--plain"],
        check=True,
        capture_output=True,
        text=True,
        input="",
    )
    assert completed.stdout.strip() == "docs_only"
