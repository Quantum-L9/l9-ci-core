from __future__ import annotations
import re
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "node-pr-pipeline.yml"
def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
def workflow_call(data: dict) -> dict:
    on_block = data.get(True) or data.get("on") or {}
    return on_block.get("workflow_call", {})
def test_node_pipeline_exists_and_parses() -> None:
    assert WORKFLOW_PATH.is_file()
    data = load_workflow()
    assert isinstance(data, dict)
    assert "jobs" in data
def test_node_pipeline_is_reusable_only() -> None:
    data = load_workflow()
    on_block = data.get(True) or data.get("on") or {}
    assert "workflow_call" in on_block
    assert "pull_request_target" not in on_block
def test_node_pipeline_public_inputs() -> None:
    inputs = workflow_call(load_workflow()).get("inputs", {})
    expected = {
        "node-version": ("string", "20"),
        "working-directory": ("string", "."),
        "install-command": ("string", "npm ci"),
        "lint-script": ("string", "lint"),
        "typecheck-script": ("string", "typecheck"),
        "build-script": ("string", "build"),
        "test-script": ("string", "test"),
        "require-tests": ("boolean", True),
        "audit-level": ("string", "high"),
        "audit-production-only": ("boolean", False),
        "upload-sbom": ("boolean", True),
    }
    assert set(inputs) == set(expected)
    for input_name, (expected_type, expected_default) in expected.items():
        input_definition = inputs[input_name]
        assert input_definition["required"] is False
        assert input_definition["type"] == expected_type
        assert input_definition["default"] == expected_default
def test_node_pipeline_has_no_required_secrets() -> None:
    call = workflow_call(load_workflow())
    secrets = call.get("secrets", {})
    assert not secrets
def test_node_pipeline_has_expected_jobs() -> None:
    jobs = load_workflow()["jobs"]
    assert set(jobs) == {
        "validate",
        "lint",
        "test",
        "security",
        "sbom",
        "ci-gate",
    }
def test_node_pipeline_has_read_only_top_level_permissions() -> None:
    data = load_workflow()
    assert data["permissions"] == {"contents": "read"}
def test_node_pipeline_has_cancellation_concurrency() -> None:
    concurrency = load_workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "github.ref" in concurrency["group"]
def test_every_node_pipeline_job_has_timeout() -> None:
    jobs = load_workflow()["jobs"]
    for job_name, job in jobs.items():
        assert "timeout-minutes" in job, job_name
        assert isinstance(job["timeout-minutes"], int), job_name
        assert job["timeout-minutes"] > 0, job_name
def test_every_action_reference_is_sha_pinned() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\s*(?:-\s*)?uses:\s*([^\s#]+)@([^\s#@]+)\s*(#.*)?$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    assert matches
    for match in matches:
        action, reference, comment = match.groups()
        assert re.fullmatch(r"[0-9a-f]{40}", reference), (
            f"{action}@{reference} is not SHA pinned"
        )
        assert comment and re.search(r"#\s*v?\d", comment), (
            f"{action}@{reference} is missing a version comment"
        )
def test_node_pipeline_has_no_python_runtime_dependency() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    forbidden_fragments = (
        "actions/setup-python",
        "pip install",
        "python -m pip",
        "l9-ci ",
        "pytest",
        "ruff ",
        "mypy ",
    )
    for fragment in forbidden_fragments:
        assert fragment not in text
def test_node_pipeline_uses_npm_lockfile_cache() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "cache: npm" in text
    assert (
        "cache-dependency-path: "
        "${{ inputs.working-directory }}/package-lock.json"
    ) in text
def test_install_command_is_not_directly_interpolated_into_shell() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "L9_INSTALL_COMMAND: ${{ inputs.install-command }}" in text
    assert 'bash -euo pipefail -c "$L9_INSTALL_COMMAND"' in text
    assert "run: ${{ inputs.install-command }}" not in text
def test_node_pipeline_final_gate_is_strict() -> None:
    gate = load_workflow()["jobs"]["ci-gate"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {
        "validate",
        "lint",
        "test",
        "security",
        "sbom",
    }
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert 'if [ "$status" != "success" ]; then' in text
    assert "success|skipped" not in text.split(
        "ci-gate:",
        maxsplit=1,
    )[1]
def test_required_jobs_do_not_use_job_level_skip_conditions() -> None:
    jobs = load_workflow()["jobs"]
    for job_name in ("validate", "lint", "test", "security", "sbom"):
        assert "if" not in jobs[job_name], job_name
def test_node_pipeline_job_permissions_are_least_privilege() -> None:
    jobs = load_workflow()["jobs"]
    assert jobs["validate"]["permissions"] == {"contents": "read"}
    assert jobs["lint"]["permissions"] == {"contents": "read"}
    assert jobs["test"]["permissions"] == {"contents": "read"}
    assert jobs["ci-gate"]["permissions"] == {"contents": "read"}
    assert jobs["security"]["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert jobs["sbom"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
def test_node_pipeline_validates_audit_level() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "low|moderate|high|critical" in text
    assert "--audit-level=${L9_AUDIT_LEVEL}" in text
def test_node_pipeline_requires_package_contract() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "package.json is required" in text
    assert "package-lock.json is required" in text
def test_dependency_review_is_pull_request_only() -> None:
    security_steps = load_workflow()["jobs"]["security"]["steps"]
    dependency_review = next(
        step
        for step in security_steps
        if step.get("name") == "Dependency Review"
    )
    assert dependency_review["if"] == "github.event_name == 'pull_request'"
    assert dependency_review["continue-on-error"] is True
def test_gitleaks_download_is_checksum_verified() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "gitleaks_8.30.1_checksums.txt" not in text
    assert 'gitleaks_${GITLEAKS_VERSION}_checksums.txt' in text
    assert "sha256sum --check -" in text
    assert "set -euo pipefail" in text
def test_gitleaks_runs_with_redact_flag() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "--redact" in text
def test_sbom_step_conditionals_use_expression_syntax() -> None:
    steps = load_workflow()["jobs"]["sbom"]["steps"]
    for step in steps:
        if "if" in step:
            condition = str(step["if"])
            assert not condition.startswith("inputs."), (
                f"sbom step '{step.get('name')}' uses bare inputs.* condition "
                f"instead of ${{{{ }}}} expression syntax: {condition}"
            )
