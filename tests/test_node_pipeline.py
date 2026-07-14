"""Static contract tests for the reusable Node.js/TypeScript PR pipeline.

These assertions freeze the public interface and the security invariants of
``.github/workflows/node-pr-pipeline.yml``. The workflow is additive and must
never drag Python tooling (setup-python, pip, the l9-ci CLI) into a Node run.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
NODE_WORKFLOW = WORKFLOWS / "node-pr-pipeline.yml"

EXPECTED_JOBS = ["validate", "lint", "test", "security", "semgrep", "sbom", "ci-gate"]

# The frozen public interface: (type, default) per input. ``required`` is False
# for every input in v1.
EXPECTED_INPUTS = {
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
    "run-semgrep": ("boolean", True),
    "upload-sbom": ("boolean", True),
}


def _load() -> dict:
    return yaml.safe_load(NODE_WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return NODE_WORKFLOW.read_text(encoding="utf-8")


def _on_block(data: dict) -> dict:
    # PyYAML parses the bare ``on:`` key as the boolean True.
    return data.get(True) or data.get("on") or {}


def _workflow_call_inputs(data: dict) -> dict:
    return _on_block(data).get("workflow_call", {}).get("inputs", {})


# 1. The file parses as YAML.
def test_parses_as_yaml() -> None:
    data = _load()
    assert isinstance(data, dict)
    assert "jobs" in data


# 2. It declares on.workflow_call.
def test_declares_workflow_call() -> None:
    on_block = _on_block(_load())
    assert isinstance(on_block, dict)
    assert "workflow_call" in on_block


# 3. It does not declare pull_request_target.
def test_no_pull_request_target() -> None:
    on_block = _on_block(_load())
    keys = on_block if isinstance(on_block, (dict, list)) else []
    assert "pull_request_target" not in keys
    assert "pull_request_target" not in _text()


# 4. The documented public inputs have the correct types and defaults.
def test_public_inputs_types_and_defaults() -> None:
    inputs = _workflow_call_inputs(_load())
    assert set(inputs) == set(EXPECTED_INPUTS), (
        f"input set drifted: {sorted(set(inputs) ^ set(EXPECTED_INPUTS))}"
    )
    for name, (exp_type, exp_default) in EXPECTED_INPUTS.items():
        spec = inputs[name]
        assert spec.get("type") == exp_type, f"{name}: type {spec.get('type')!r}"
        assert spec.get("default") == exp_default, f"{name}: default {spec.get('default')!r}"
        assert bool(spec.get("required", False)) is False, f"{name}: must be optional"


# 5. It has no required secrets.
def test_no_required_secrets() -> None:
    secrets = _on_block(_load()).get("workflow_call", {}).get("secrets", {}) or {}
    for name, spec in secrets.items():
        assert not (spec or {}).get("required", False), f"secret {name} must not be required"


# 6. Top-level permissions are read-only.
def test_top_level_permissions_read_only() -> None:
    perms = _load().get("permissions", {})
    assert perms == {"contents": "read"}


# 7. Top-level concurrency exists with cancellation enabled.
def test_concurrency_cancels_in_progress() -> None:
    concurrency = _load().get("concurrency", {})
    assert concurrency.get("group")
    assert concurrency.get("cancel-in-progress") is True


# 8. Expected jobs exist.
def test_expected_jobs_exist() -> None:
    jobs = _load()["jobs"]
    assert set(jobs) == set(EXPECTED_JOBS)


# 9. Every job has timeout-minutes.
def test_every_job_has_timeout() -> None:
    for name, job in _load()["jobs"].items():
        assert "timeout-minutes" in job, f"job {name} missing timeout-minutes"


# 10. Every uses: reference is pinned to a 40-char hexadecimal SHA.
# 11. Every pinned action has a version comment.
def test_all_actions_sha_pinned_with_version_comment() -> None:
    floating = re.compile(
        r"^\s*(?:-\s*)?uses:\s*([^\s#]+)@([^\s#@]+)\s*(#.*)?$", re.MULTILINE
    )
    matches = floating.findall(_text())
    assert matches, "expected at least one uses: reference"
    for action, ref, comment in matches:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{action}@{ref} not SHA-pinned"
        assert comment and re.search(r"#\s*v?\d", comment), (
            f"{action}@{ref} missing version comment"
        )


# 12. The workflow contains no Python setup action.
def test_no_python_setup_action() -> None:
    assert "actions/setup-python" not in _text()


# 13. The workflow contains no pip install.
def test_no_pip_install() -> None:
    assert not re.search(r"\bpip(?:3)?\s+install\b", _text())


# 14. The workflow contains no invocation of the Python l9-ci CLI. (The repo
#     provenance strings like ``l9-ci-core`` in comments are not CLI calls, so
#     this is scoped to executable ``run:`` bodies.)
def test_no_l9_ci_cli() -> None:
    for name, job in _load()["jobs"].items():
        for step in job.get("steps", []):
            run_body = str(step.get("run", ""))
            assert not re.search(r"\bl9-ci\b", run_body), (
                f"job {name} invokes the l9-ci CLI in a run step"
            )


# 15. The final gate has if: always().
def test_ci_gate_if_always() -> None:
    gate = _load()["jobs"]["ci-gate"]
    assert str(gate.get("if", "")).strip() == "always()"


# 16. The final gate depends on all required jobs.
def test_ci_gate_needs_all_jobs() -> None:
    needs = _load()["jobs"]["ci-gate"]["needs"]
    required = [j for j in EXPECTED_JOBS if j != "ci-gate"]
    assert set(needs) == set(required)


# 17. The final gate requires success rather than accepting skipped jobs.
def test_ci_gate_requires_success_not_skipped() -> None:
    gate = _load()["jobs"]["ci-gate"]
    run_text = "".join(str(step.get("run", "")) for step in gate["steps"])
    # Success is the only accepted result; the gate must not special-case
    # 'skipped' as acceptable the way per-job aggregates legitimately do.
    assert '!= "success"' in run_text or "!= 'success'" in run_text
    assert "skipped) : ;;" not in run_text
    assert "success|skipped" not in run_text


# 18. The npm cache dependency path incorporates working-directory.
def test_cache_dependency_path_uses_working_directory() -> None:
    assert (
        "cache-dependency-path: ${{ inputs.working-directory }}/package-lock.json"
        in _text()
    )


# 19. require-tests defaults to true.
def test_require_tests_default_true() -> None:
    inputs = _workflow_call_inputs(_load())
    assert inputs["require-tests"]["default"] is True


# 20. audit-production-only defaults to false.
def test_audit_production_only_default_false() -> None:
    inputs = _workflow_call_inputs(_load())
    assert inputs["audit-production-only"]["default"] is False


# 21. Arbitrary command input is passed through an environment variable rather
#     than inserted directly into a multiline shell command.
def test_install_command_passed_via_env() -> None:
    text = _text()
    assert "L9_INSTALL_COMMAND: ${{ inputs.install-command }}" in text
    assert 'bash -euo pipefail -c "$L9_INSTALL_COMMAND"' in text
    # The raw expansion must never be spliced into a run: script body.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "${{ inputs.install-command }}" in line:
            assert stripped.startswith("L9_INSTALL_COMMAND:"), (
                f"install-command expanded outside env binding: {line!r}"
            )


# 22. No job has write permissions unless explicitly justified (none in v1).
def test_no_job_has_write_permissions() -> None:
    data = _load()
    top = data.get("permissions", {})
    assert "write" not in str(top).lower()
    for name, job in data["jobs"].items():
        perms = job.get("permissions", {})
        assert perms, f"job {name} must declare least-privilege permissions"
        for scope, level in perms.items():
            assert level != "write", f"job {name} grants write on {scope}"


FIXTURES = ROOT / "tests" / "fixtures" / "node-consumers"


def _validate_consumer(working_directory: Path, *, test_script: str = "test",
                       require_tests: bool = True) -> list[str]:
    """Port of the workflow's validate-job contract check, so the fixtures
    exercise the exact invariants the pipeline enforces at runtime."""
    import json

    errors: list[str] = []
    if not working_directory.is_dir():
        return [f"working-directory {working_directory} does not exist"]
    pkg_path = working_directory / "package.json"
    lock_path = working_directory / "package-lock.json"
    if not pkg_path.exists():
        errors.append("package.json missing")
        return errors
    if not lock_path.exists():
        errors.append("package-lock.json missing")
        return errors
    try:
        pkg = json.loads(pkg_path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        return [f"package.json invalid JSON: {exc}"]
    try:
        lock = json.loads(lock_path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        return [f"package-lock.json invalid JSON: {exc}"]
    if not lock.get("lockfileVersion"):
        errors.append("package-lock.json has no lockfileVersion")
    scripts = pkg.get("scripts") or {}
    if require_tests and test_script and test_script not in scripts:
        errors.append(f"required test script {test_script!r} missing")
    return errors


# The minimal fixture is a valid root-level consumer.
def test_minimal_fixture_is_a_valid_consumer() -> None:
    assert _validate_consumer(FIXTURES / "minimal") == []


# Section 11: a non-root working-directory must resolve the correct package and
# lockfile. The monorepo fixture places the package under packages/service-a.
def test_non_root_working_directory_resolves() -> None:
    service = FIXTURES / "monorepo" / "packages" / "service-a"
    assert (service / "package.json").exists()
    assert (service / "package-lock.json").exists()
    assert _validate_consumer(service) == []
    # The repository root of the monorepo fixture is NOT itself a consumer.
    assert _validate_consumer(FIXTURES / "monorepo") != []


# require-tests semantics: a missing test script is an error only when required.
def test_missing_test_script_requires_tests_semantics() -> None:
    # minimal has a 'test' script; ask for a script it does not define.
    assert _validate_consumer(FIXTURES / "minimal", test_script="ci-test",
                              require_tests=True) != []
    assert _validate_consumer(FIXTURES / "minimal", test_script="ci-test",
                              require_tests=False) == []


# Provenance: the setup-node pin must be recorded in the action inventory.
def test_setup_node_is_inventoried() -> None:
    import json

    inventory = json.loads(
        (ROOT / ".github" / "governance" / "action-pins.lock.json").read_text("utf-8")
    )
    entry = inventory["entries"].get("actions/setup-node")
    assert entry, "actions/setup-node must be in action-pins.lock.json"
    m = re.search(
        r"actions/setup-node@([0-9a-f]{40})", _text()
    )
    assert m, "workflow must SHA-pin actions/setup-node"
    assert m.group(1) == entry["commit_sha"]
