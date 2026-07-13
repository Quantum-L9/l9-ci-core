from __future__ import annotations

import json
import re
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


# After PR-A, every external action is pinned to a 40-hex commit SHA with the
# human-readable version carried in a trailing ``# vX.Y.Z`` annotation. The
# original intent of this test -- "these workflows use v6 of checkout /
# setup-python, not the older v4/v5" -- is preserved, but expressed against the
# stronger SHA-pinning invariant rather than a mutable floating tag. When a test
# and a security invariant disagree, the invariant wins and the test is updated.
# Git commit SHAs are hex and case-insensitive, so accept A-F as well as a-f to
# avoid brittle failures if a pin is pasted with uppercase characters.
_CHECKOUT_V6 = re.compile(r"actions/checkout@[0-9a-fA-F]{40}\s+#\s*v6(?:\.\d+)*")
_SETUP_PY_V6 = re.compile(r"actions/setup-python@[0-9a-fA-F]{40}\s+#\s*v6(?:\.\d+)*")


def test_new_workflows_use_v6_actions() -> None:
    for name in ["nightly.yml", "pre-commit-ci.yml", "release-publish.yml"]:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        # Stronger invariant: SHA-pinned, annotated v6.
        assert _CHECKOUT_V6.search(text), f"{name}: checkout not SHA-pinned to annotated v6"
        assert _SETUP_PY_V6.search(text), f"{name}: setup-python not SHA-pinned to annotated v6"
        # No floating tags of any kind for these two actions (SHA-only).
        assert "actions/checkout@v" not in text
        assert "actions/setup-python@v" not in text
        # No older major versions in the checkout/setup-python annotations.
        assert not re.search(r"actions/checkout@[0-9a-fA-F]{40}\s+#\s*v[45]\b", text)
        assert not re.search(r"actions/setup-python@[0-9a-fA-F]{40}\s+#\s*v5\b", text)


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
    forbidden = ["contracts/transport-packet.schema.json", "scripts/generate_schema.py", "scripts/validate_contracts.py"]
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
