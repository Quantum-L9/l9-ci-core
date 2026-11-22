"""Core is an ordinary V2 consumer of its own ABI, with V1 behavior preserved.

Setup, validate, and check reproduce what the retired V1 command matrices did.
``test`` is the explicit PR unittest subset. ``test-deep`` is the full
unittest discover over ``tests/``.

    setup    -> pip install requirements-ci.txt, requirements-repo-runtime.txt
    validate -> structural validation + workflow-integrity checker
    check    -> toolchain preflight, ruff check, ruff format --check, mypy
    test     -> explicit unittest modules (the PR subset)
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import ROOT, make, v2_contract  # noqa: E402

from l9_repo.__main__ import CoreRepositoryExecution  # noqa: E402
from l9_repo.contract import (  # noqa: E402
    FACADE_TARGETS,
    REQUIRED_LEAVES,
    RepositoryExecution,
    make_phony_targets,
    make_target_declarations,
    render_facade,
)

BRIDGE = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
REPO_MK = ROOT / "Repo.mk"

# Frozen PR subset. ``make test`` must invoke these modules explicitly.
REPO_TEST_MODULES = (
    "tests/actions/test_aggregate_required_instances.py",
    "tests/actions/test_run_supply_chain.py",
    "tests/actions/test_plan_required_instances.py",
    "tests/actions/test_repository_verification_conflict_markers.py",
    "tests/governance/test_ci_runtime_invariants.py",
    "tests/governance/test_core_toolchain_alignment.py",
    "tests/governance/test_dependabot_core_owned_tools.py",
    "tests/repo_execution/test_boundary.py",
    "tests/repo_execution/test_contract.py",
    "tests/repo_execution/test_contract_admission.py",
    "tests/repo_execution/test_core_policy.py",
    "tests/repo_execution/test_core_runtime.py",
    "tests/repo_execution/test_core_self_hosting.py",
    "tests/repo_execution/test_facade.py",
    "tests/repo_execution/test_inherited_config.py",
    "tests/repo_execution/test_legacy_v1.py",
    "tests/repo_execution/test_repo_mk.py",
    "tests/repo_execution/test_stamp.py",
    "tests/settings_engine/test_settings_requirements.py",
    "tests/workflows/test_org_ci_security_checks.py",
    "tests/workflows/test_org_ci_hygiene_checks.py",
    "tests/test_unittest_collectability.py",
    "tests/tools/test_advance_core_action_pins.py",
    "tests/tools/test_audit_core_toolchain.py",
    "tests/tools/test_check_core_pin_ancestry.py",
    "tests/tools/test_check_toolchain_versions.py",
    "tests/tools/test_check_workflow_integrity.py",
    "tests/tools/test_manifest_integrity.py",
    "tests/tools/test_nightly_sink.py",
    "tests/tools/test_project_required_repositories.py",
    "tests/tools/test_tool_ownership.py",
    "tests/workflows/test_merge_queue_triggers.py",
    "tests/workflows/test_required_check_name.py",
    "tests/workflows/test_nightly_apply.py",
    "tests/workflows/test_org_ci_toolchain_drift_report.py",
    "tests/workflows/test_signal_law_conformance.py",
    "tests/workflows/test_signal_law_failures.py",
    "tests/workflows/test_central_semgrep_required_instances.py",
    "tests/workflows/test_org_ci_biome_job.py",
    "tests/workflows/test_org_ci_codeql_job.py",
    "tests/workflows/test_org_ci_actionlint_job.py",
    "tests/workflows/test_instance_runner_scan_root_paths.py",
    "tests/workflows/test_security_consumer_scope.py",
    "tests/workflows/test_gitleaks_core_rules.py",
    "tests/workflows/test_security_pip_audit_lockset.py",
    "tests/workflows/test_blocking_check_classification.py",
    "tests/workflows/test_security_scan_outcomes.py",
)
EXPLICIT_UNITTEST = "-m unittest --verbose " + " ".join(REPO_TEST_MODULES)
FULL_DISCOVER = (
    "-m unittest discover --start-directory tests --pattern 'test_*.py' --verbose"
)

# Setup, validate, and check stay on the V1 command matrices. Test is the
# explicit module invocation. Order within a phase is asserted too.
V1_PARITY: dict[str, list[str]] = {
    "setup": [
        "-m pip install -r requirements-ci.txt",
        "-m pip install -r requirements-repo-runtime.txt",
    ],
    "validate": [
        "-m tools.l9_repo",
        "validate",
        "core-validate",
        "tools/check_workflow_integrity.py",
    ],
    "check": [
        "tools/check_toolchain_versions.py",
        "-m ruff check .",
        "-m ruff format --check .",
        "-m mypy",
    ],
    "test": [EXPLICIT_UNITTEST],
}


@unittest.skipUnless(shutil.which("make"), "GNU make is not installed")
class CorePhaseParityTests(unittest.TestCase):
    def dry_run(self, phase: str) -> str:
        result = make(ROOT, "-n", phase)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def test_each_phase_reproduces_its_v1_commands_in_order(self) -> None:
        for phase, fragments in V1_PARITY.items():
            with self.subTest(phase=phase):
                output = self.dry_run(phase).replace("'", "")
                position = -1
                for fragment in fragments:
                    found = output.find(fragment, position + 1)
                    self.assertGreater(
                        found, position, f"{fragment!r} missing/out of order"
                    )
                    position = found

    def test_test_deep_dry_run_contains_the_full_discover_command(self) -> None:
        self.assertIn(FULL_DISCOVER, self.dry_run("test-deep"))

    def test_check_resolves_tools_through_the_interpreter(self) -> None:
        # $(PYTHON) may be a bare name or an absolute interpreter path; either
        # way the first token is the interpreter, never a bare ruff/mypy.
        for line in self.dry_run("check").splitlines():
            with self.subTest(line=line):
                interpreter = pathlib.PurePosixPath(line.split()[0]).name
                self.assertTrue(interpreter.startswith("python"), line)
                self.assertNotIn(interpreter, {"ruff", "mypy"})

    def test_check_runs_the_toolchain_preflight_first(self) -> None:
        first = self.dry_run("check").splitlines()[0]
        self.assertIn("tools/check_toolchain_versions.py", first)

    def test_phases_never_reach_publication(self) -> None:
        for phase in V1_PARITY:
            with self.subTest(phase=phase):
                output = self.dry_run(phase)
                for token in ("git push", "gh pr", "l9 pr", " l9 "):
                    self.assertNotIn(token, output)


class CoreConsumerTests(unittest.TestCase):
    def test_core_contract_is_the_exact_v2_declaration(self) -> None:
        data = json.loads((ROOT / ".l9/repo-workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(v2_contract(), data)
        self.assertNotIn("schema_version", data)

    def test_core_makefile_is_the_released_facade(self) -> None:
        self.assertEqual(render_facade("make-v1"), (ROOT / "Makefile").read_bytes())

    def test_core_passes_generic_v2_validation(self) -> None:
        RepositoryExecution(ROOT).validate()

    def test_core_policy_loads(self) -> None:
        policy = CoreRepositoryExecution(ROOT).policy()
        self.assertEqual("origin/main", policy["comparison_ref"])

    def test_core_repo_mk_is_repository_owned(self) -> None:
        text = REPO_MK.read_text(encoding="utf-8")
        self.assertIn("REPOSITORY-OWNED", text)
        self.assertNotIn("GENERATED", text)
        self.assertNotIn("DO NOT EDIT", text)
        self.assertNotIn("make-plan", text)
        self.assertNotIn("Repo.local.mk", text)
        self.assertFalse((ROOT / "Repo.local.mk").exists())

    def test_core_repo_mk_declares_phony_leaves_and_no_facade_verbs(self) -> None:
        text = REPO_MK.read_text(encoding="utf-8")
        declared = {target for _, target in make_target_declarations(text)}
        phony = make_phony_targets(text)
        self.assertLessEqual(set(REQUIRED_LEAVES), declared)
        self.assertLessEqual(set(REQUIRED_LEAVES), phony)
        self.assertEqual(set(), declared & FACADE_TARGETS)
        self.assertEqual(declared, phony, "every Core-local target is .PHONY")

    def test_admission_bridge_classifies_core_as_v2(self) -> None:
        spec = importlib.util.spec_from_file_location("l9_bridge_for_core", BRIDGE)
        assert spec is not None and spec.loader is not None
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        document = bridge.load_contract(ROOT / ".l9/repo-workflow.json")
        self.assertEqual("v2", bridge.classify_contract(document))


if __name__ == "__main__":
    unittest.main()
