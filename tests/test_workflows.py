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


# Actions are pinned to full 40-char commit SHAs with a trailing
# "# vX.Y.Z" comment recording the human-readable version, e.g.:
#   uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6.0.3
# These helpers match that pattern instead of asserting on floating tags,
# since floating tags (@v6, @v4, ...) are no longer permitted in this repo.
def _pinned_major_versions(text: str, action: str) -> set[str]:
    pattern = re.compile(rf"uses:\s*{re.escape(action)}" + r"@([0-9a-f]{40})\s*#\s*v(\d+)\.")
    return {major for _, major in pattern.findall(text)}


def test_new_workflows_use_v6_actions() -> None:
    for name in ["nightly.yml", "pre-commit-ci.yml", "release-publish.yml"]:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")

        checkout_majors = _pinned_major_versions(text, "actions/checkout")
        setup_python_majors = _pinned_major_versions(text, "actions/setup-python")

        assert checkout_majors == {"6"}, f"{name}: expected only checkout v6 pins, found majors {checkout_majors}"
        assert setup_python_majors == {"6"}, f"{name}: expected only setup-python v6 pins, found majors {setup_python_majors}"

        # No floating (unpinned) references to these actions may remain.
        assert not re.search(r"uses:\s*actions/checkout@v\d", text), f"{name}: found unpinned actions/checkout tag"
        assert not re.search(r"uses:\s*actions/setup-python@v\d", text), f"{name}: found unpinned actions/setup-python tag"


def test_all_action_references_are_sha_pinned() -> None:
    """Every `uses:` reference in every workflow must be a 40-char commit
    SHA with a version comment; no floating tag or branch pointer allowed."""
    floating_pattern = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)@([^\s#@]+)\s*(#.*)?$", re.MULTILINE)
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for match in floating_pattern.finditer(text):
            action, ref, comment = match.groups()
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"{path.name}: {action}@{ref} is not SHA-pinned"
            )
            assert comment and re.search(r"#\s*v?\d", comment), (
                f"{path.name}: {action}@{ref} is missing a version comment"
            )


def test_release_publish_supports_pypi_token_fallback() -> None:
    """release-publish.yml is a reusable workflow (workflow_call), so PyPI's
    OIDC trusted-publisher identity binding does not resolve correctly for
    any real caller -- the workflow must also support an api-token fallback
    so releases are not silently blocked. See docs/RELEASE_WORKFLOW.md."""
    data = load_workflow("release-publish.yml")
    inputs = workflow_call_inputs(data)
    assert "pypi-publish-mode" in inputs
    assert inputs["pypi-publish-mode"]["default"] == "trusted-publisher"

    on_block = data.get(True) or data.get("on") or {}
    call_secrets = on_block.get("workflow_call", {}).get("secrets", {})
    assert "pypi-api-token" in call_secrets
    assert call_secrets["pypi-api-token"].get("required") is False

    publish_job = data["jobs"]["publish"]
    step_names = [step.get("name") for step in publish_job["steps"]]
    assert "Publish to PyPI with Trusted Publisher (OIDC)" in step_names
    assert "Publish to PyPI with API token (reusable-workflow fallback)" in step_names

    text = (WORKFLOWS / "release-publish.yml").read_text(encoding="utf-8")
    assert "secrets.pypi-api-token" in text
    assert "pypi-publish-mode == 'trusted-publisher'" in text
    assert "pypi-publish-mode == 'api-token'" in text


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
