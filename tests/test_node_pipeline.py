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


# ── original tests (unchanged) ────────────────────────────────────────────────

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
    expected_base = {
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
    expected_context = {
        "changed-files": ("string", ""),
        "pr-labels": ("string", ""),
        "labels-known": ("boolean", True),
    }
    expected_sdk = {
        "l9-ci-install-command": ("string", None),
    }
    for input_name, (expected_type, expected_default) in expected_base.items():
        assert input_name in inputs
        assert inputs[input_name]["type"] == expected_type
        assert inputs[input_name]["default"] == expected_default
    for input_name, (expected_type, _) in expected_context.items():
        assert input_name in inputs, f"G-01 input missing: {input_name}"
        assert inputs[input_name]["type"] == expected_type
    for input_name, _ in expected_sdk.items():
        assert input_name in inputs, f"G-02 input missing: {input_name}"


def test_node_pipeline_has_no_required_secrets() -> None:
    call = workflow_call(load_workflow())
    secrets = call.get("secrets", {})
    for name, defn in secrets.items():
        assert defn.get("required") is not True, f"secret {name} must not be required"


def test_node_pipeline_has_expected_jobs() -> None:
    jobs = load_workflow()["jobs"]
    assert set(jobs) == {
        "validate",
        "lint",
        "semgrep",
        "test",
        "security",
        "sbom",
        "scorecard",
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


def test_node_pipeline_final_gate_needs_all_required_jobs() -> None:
    """All 7 pipeline jobs must be declared in ci-gate needs — no silent skips."""
    gate = load_workflow()["jobs"]["ci-gate"]
    assert set(gate["needs"]) == {
        "validate", "lint", "semgrep", "test", "security", "sbom", "scorecard"
    }


def test_ci_gate_uses_skipped_tolerant_logic() -> None:
    """G-06: ci-gate must never fail on 'skipped' job results."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    gate_section = text.split("ci-gate:", maxsplit=1)[1]
    assert 'if [ "$status" != "success" ]; then' not in gate_section, (
        "ci-gate must not use != success; use l9-ci gate or success|skipped case match"
    )


def test_node_pipeline_ci_gate_contract() -> None:
    """E-02: ci-gate must always(), need all 7 jobs, delegate to l9-ci gate."""
    gate = load_workflow()["jobs"]["ci-gate"]
    assert gate["if"] == "always()"
    expected = {"validate", "lint", "semgrep", "test", "security", "sbom", "scorecard"}
    assert set(gate["needs"]) == expected
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    gate_section = text.split("ci-gate:", maxsplit=1)[1]
    assert "l9-ci gate" in gate_section, "ci-gate must call l9-ci gate for result evaluation"
    assert 'if [ "$status" != "success" ]; then' not in gate_section


def test_required_jobs_do_not_use_job_level_skip_conditions() -> None:
    jobs = load_workflow()["jobs"]
    for job_name in ("validate", "lint", "test", "security", "sbom", "semgrep"):
        assert "if" not in jobs[job_name], job_name


def test_node_pipeline_job_permissions_are_least_privilege() -> None:
    jobs = load_workflow()["jobs"]
    assert jobs["validate"]["permissions"] == {"contents": "read"}
    assert jobs["lint"]["permissions"] == {"contents": "read"}
    assert jobs["test"]["permissions"] == {"contents": "read"}
    assert jobs["semgrep"]["permissions"] == {"contents": "read"}
    # E-03: security must include security-events: write (G-11)
    assert jobs["security"]["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "security-events": "write",
    }
    # G-11: sbom and scorecard need id-token: write
    assert jobs["sbom"]["permissions"].get("id-token") == "write"
    assert jobs["scorecard"]["permissions"].get("security-events") == "write"
    assert jobs["scorecard"]["permissions"].get("id-token") == "write"


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


# ── G-01 through G-12 ─────────────────────────────────────────────────────────

def test_g01_context_inputs_present() -> None:
    """G-01: context routing inputs must be declared."""
    inputs = workflow_call(load_workflow()).get("inputs", {})
    for name in ("changed-files", "pr-labels", "labels-known"):
        assert name in inputs, f"G-01: missing context input '{name}'"


def test_g02_sdk_install_command_input_present() -> None:
    """G-02: l9-ci-install-command input must be declared."""
    inputs = workflow_call(load_workflow()).get("inputs", {})
    assert "l9-ci-install-command" in inputs, "G-02: missing l9-ci-install-command input"
    assert inputs["l9-ci-install-command"]["type"] == "string"


def test_g02_sdk_stage_runners_present() -> None:
    """G-02: each main job must invoke l9-ci run-pipeline --stage."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    for stage in ("validate", "lint", "test", "security"):
        assert f"--stage {stage}" in text, f"G-02: l9-ci stage runner missing for '{stage}'"


def test_g03_ci_gate_downloads_artifacts() -> None:
    """G-03: ci-gate must download ci-summary artifacts before evaluating."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    gate_section = text.split("ci-gate:", maxsplit=1)[1]
    assert "download-artifact" in gate_section, (
        "G-03: ci-gate must use actions/download-artifact to aggregate ci-summary artifacts"
    )


def test_g03_ci_gate_emits_agent_payload() -> None:
    """G-03: ci-gate must emit agent_review_payload.json."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    gate_section = text.split("ci-gate:", maxsplit=1)[1]
    assert "agent_review_payload.json" in gate_section, (
        "G-03: ci-gate must emit agent_review_payload.json for agent consumption"
    )


def test_g04_validate_has_l9_governance_steps() -> None:
    """G-04: validate job must run L9 governance checks."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    validate_section = text.split("validate:", maxsplit=1)[1].split("\n  lint:", maxsplit=1)[0]
    for check in ("check-transport-packet", "check-deprecated-api", "validate-thresholds"):
        assert check in validate_section, f"G-04: validate job missing l9-ci check '{check}'"


def test_g05_advisory_npm_audit_present() -> None:
    """G-05: security job must have an advisory (non-blocking) npm audit step."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "--audit-level=none" in text, (
        "G-05: advisory npm audit (--audit-level=none) must be present"
    )


def test_g07_semgrep_job_present() -> None:
    """G-07: semgrep job must exist."""
    jobs = load_workflow()["jobs"]
    assert "semgrep" in jobs, "G-07: semgrep job missing"
    steps = jobs["semgrep"]["steps"]
    step_text = str(steps)
    assert "semgrep" in step_text.lower()


def test_g08_scorecard_job_present() -> None:
    """G-08: OpenSSF scorecard job must exist."""
    jobs = load_workflow()["jobs"]
    assert "scorecard" in jobs, "G-08: scorecard job missing"
    steps = jobs["scorecard"]["steps"]
    uses_list = [s.get("uses", "") for s in steps]
    assert any("scorecard-action" in u for u in uses_list), (
        "G-08: scorecard job must use ossf/scorecard-action"
    )


def test_g09_artifact_uploads_in_all_main_jobs() -> None:
    """G-09: validate, lint, test, security jobs must upload ci-summary artifacts."""
    jobs = load_workflow()["jobs"]
    for job_name in ("validate", "lint", "test", "security"):
        step_names = [s.get("name", "") for s in jobs[job_name]["steps"]]
        assert any("Upload" in n and "Summary" in n for n in step_names), (
            f"G-09: {job_name} job missing ci-summary artifact upload step"
        )


def test_g10_sdk_auth_step_in_all_sdk_jobs() -> None:
    """G-10: every job that installs the SDK must have the private SDK access step."""
    jobs = load_workflow()["jobs"]
    sdk_jobs = ("validate", "lint", "test", "security", "ci-gate")
    for job_name in sdk_jobs:
        step_names = [s.get("name", "") for s in jobs[job_name]["steps"]]
        assert any("private SDK access" in n for n in step_names), (
            f"G-10: {job_name} job missing 'Configure private SDK access' step"
        )


def test_g11_security_has_security_events_write() -> None:
    """G-11: security job must have security-events: write permission."""
    jobs = load_workflow()["jobs"]
    assert jobs["security"]["permissions"].get("security-events") == "write", (
        "G-11: security job must declare security-events: write"
    )
