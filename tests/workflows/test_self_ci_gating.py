"""Core's own pull requests are gated by Core's own contract (AGENTS.md §29).

Three defects were found at ``9bd8863`` and each is locked here so it cannot
return silently:

A. ``self-ci.yml``'s ``Unit tests`` job ran the suite on the runner's system
   Python with nothing installed, so the cases that need PyYAML, jsonschema or
   the pinned ruff/mypy depended on the runner image, and the real-toolchain
   cases in ``tests/tools/test_check_toolchain_versions.py`` ran nowhere.
B. No self-CI job ran Core's make-v1 repository contract
   (``make setup -> validate -> check -> test``) at the pull request's revision.
C. The Core main-protection ruleset payload required the bare context
   ``validate``, which ``governance-ci.yml`` and ``release-validation.yml``
   both produce, and omitted the contract, analysis and security checks.

``Unit tests`` runs the frozen explicit module list below. It does not run
``unittest discover`` or ``make test``. A missing listed file or a zero
collection fails that job. ``Repository contract`` still runs ``make test``.
Full discovery runs only from ``nightly-deep.yml`` via ``make test-deep``.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SELF_CI = WORKFLOWS / "self-ci.yml"
GOVERNANCE_CI = WORKFLOWS / "governance-ci.yml"
RULESET = ROOT / "docs" / "release" / "rulesets" / "core-main-protection.json"
REPO_WORKFLOW = ROOT / ".l9" / "repo-workflow.json"

REQUIREMENT_FILES = ("requirements-ci.txt", "requirements-repo-runtime.txt")

# Frozen PR-fast census, Unit tests job. Exact and ordered.
UNIT_TEST_MODULES = (
    "tests/actions/test_check_toolchain_alignment.py",
    "tests/actions/test_detect_language.py",
    "tests/actions/test_install_biome.py",
    "tests/actions/test_install_consumer_ci.py",
    "tests/actions/test_install_semgrep.py",
    "tests/actions/test_run_biome_check.py",
    "tests/actions/test_run_sonar.py",
    "tests/architecture/test_control_plane_boundary.py",
    "tests/architecture/test_external_action_pins.py",
    "tests/architecture/test_forbidden_sdk_ownership.py",
    "tests/artifacts/test_artifact_handoff.py",
    "tests/artifacts/test_artifact_integrity.py",
    "tests/artifacts/test_artifact_protocol.py",
    "tests/artifacts/test_retrieve_action.py",
    "tests/artifacts/test_route_artifacts.py",
    "tests/governance/test_constellation_scope.py",
    "tests/governance/test_consumer_metadata.py",
    "tests/governance/test_governance_boundary.py",
    "tests/governance/test_org_governance_defaults.py",
    "tests/governance/test_release_profile_strict.py",
    "tests/governance/test_resolve_governance.py",
    "tests/governance/test_self_analysis_governance_pack.py",
    "tests/governance/test_semgrep_policy_file_permissions.py",
    "tests/governance/test_semgrep_policy_logging.py",
    "tests/invocation/test_invoke_sdk.py",
    "tests/invocation/test_validate_bundle.py",
    "tests/provisioning/test_compatibility_manifest.py",
    "tests/provisioning/test_external_checkout.py",
    "tests/provisioning/test_private_repository_auth.py",
    "tests/provisioning/test_required_cli_probing.py",
    "tests/provisioning/test_runtime_directory_safety.py",
    "tests/provisioning/test_runtime_shim_path.py",
    "tests/provisioning/test_sdk_revision_validation.py",
    "tests/provisioning/test_sdk_runtime_lock.py",
    "tests/provisioning/test_sdk_source_validation.py",
    "tests/publication/test_publication_boundary.py",
    "tests/publication/test_publish_check.py",
    "tests/publication/test_render_publication.py",
    "tests/release/test_control_plane_attestation.py",
    "tests/release/test_release_contract.py",
    "tests/release/test_release_writers.py",
    "tests/release/test_validate_release_action.py",
    "tests/release_engine/test_contract_wiring.py",
    "tests/release_engine/test_integration.py",
    "tests/release_engine/test_runner.py",
    "tests/release_engine/test_units.py",
    "tests/settings_engine/test_engine.py",
    "tests/workflows/test_analyze_semgrep_event_class.py",
    "tests/workflows/test_artifact_handoff_producers.py",
    "tests/workflows/test_caller_profile_event_compatibility.py",
    "tests/workflows/test_compatibility_inputs.py",
    "tests/workflows/test_consumer_defaulting.py",
    "tests/workflows/test_container_release_action.py",
    "tests/workflows/test_org_ci_apt_archive_cache.py",
    "tests/workflows/test_org_ci_base_retarget.py",
    "tests/workflows/test_org_ci_event_resolution.py",
    "tests/workflows/test_org_ci_runtime_environment.py",
    "tests/workflows/test_org_ci_toolchain_steps.py",
    "tests/workflows/test_org_nightly_dispatch.py",
    "tests/workflows/test_org_policy_pin.py",
    "tests/workflows/test_org_repository_verification.py",
    "tests/workflows/test_org_runtime_contract.py",
    "tests/workflows/test_org_runtime_interface.py",
    "tests/workflows/test_phase_2_workflow.py",
    "tests/workflows/test_phase_3_workflows.py",
    "tests/workflows/test_phase_4_workflows.py",
    "tests/workflows/test_phase_scope.py",
    "tests/workflows/test_private_sdk_read_token.py",
    "tests/workflows/test_promotion_canary.py",
    "tests/workflows/test_registry_required_mode.py",
    "tests/workflows/test_release_plane.py",
    "tests/workflows/test_release_runner_workflow.py",
    "tests/workflows/test_repository_execution_v2_bridge.py",
    "tests/workflows/test_reusable_workflow_outputs.py",
    "tests/workflows/test_reusable_workflow_permission_chain.py",
    "tests/workflows/test_sdk_contract_check_revision.py",
    "tests/workflows/test_sdk_contract_smoke_verdict.py",
    "tests/workflows/test_self_ci_gating.py",
    "tests/workflows/test_self_sonar.py",
    "tests/workflows/test_settings_runner_workflow.py",
    "tests/workflows/test_typescript_biome_convergence.py",
    "tests/workflows/test_workflow_integrity_clean.py",
    "tests/workflows/test_workflow_permissions.py",
    "tests/workflows/test_workflow_toolchain_pins.py",
)

NIGHTLY = WORKFLOWS / "nightly-deep.yml"
# The census run, measured for Core's Sonar analysis (requirements-coverage.txt,
# .coveragerc). Coverage wraps the one run; it adds no second test run.
UNITTEST_COMMAND = (
    'python -m coverage run --parallel-mode -m unittest "${modules[@]}" --verbose'
)
SUITE_MARKER = "-m unittest"

#: The exact check-run names the ruleset payload must require. Reusable-workflow
#: callers are named ``<caller job name> / <called job name>`` by GitHub.
EXPECTED_CONTEXTS = [
    "Lint and Type Check",
    "Unit tests",
    "Repository contract",
    "Governance validate",
    "sdk-contract",
    "Analyze (semgrep) / Analyze (semgrep -> SDK)",
    "Security scan / security",
]


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def job_named(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [
        job
        for job_id, job in workflow["jobs"].items()
        if job.get("name", job_id) == name
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one job named {name!r}, got {len(matches)}"
        )
    return matches[0]


def step_index(steps: list[dict[str, Any]], predicate: Any) -> int:
    for index, step in enumerate(steps):
        if predicate(step):
            return index
    raise AssertionError("no matching step")


def setup_python(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return steps[
        step_index(
            steps, lambda s: str(s.get("uses", "")).startswith("actions/setup-python@")
        )
    ]


def listed_modules(run: str) -> list[str]:
    match = re.search(r"modules=\((.*?)\)", run, re.S)
    if match is None:
        raise AssertionError("Unit tests job has no modules array")
    return re.findall(r"tests/\S+\.py", match.group(1))


def triggers_of(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get(True, workflow.get("on"))
    assert isinstance(triggers, dict)
    return triggers


def check_run_name(workflow: dict[str, Any], job_id: str) -> str:
    job = workflow["jobs"][job_id]
    return str(job.get("name", job_id))


class UnitTestsJobTests(unittest.TestCase):
    """Defect A: the suite runs on the pinned interpreter with its requirements."""

    def setUp(self) -> None:
        self.workflow = load(SELF_CI)
        self.lint = job_named(self.workflow, "Lint and Type Check")
        self.unit = job_named(self.workflow, "Unit tests")

    def test_unit_tests_use_the_lint_jobs_pinned_python(self) -> None:
        lint_python = setup_python(self.lint["steps"])
        unit_python = setup_python(self.unit["steps"])
        self.assertRegex(unit_python["uses"], r"^actions/setup-python@[0-9a-f]{40}$")
        self.assertEqual(lint_python["uses"], unit_python["uses"])
        self.assertEqual(lint_python["with"], unit_python["with"])
        self.assertEqual("3.12", unit_python["with"]["python-version"])

    def test_job_names_are_the_three_gates_and_the_sonar_caller(self) -> None:
        names = [
            job.get("name", job_id) for job_id, job in self.workflow["jobs"].items()
        ]
        self.assertEqual(
            [
                "Lint and Type Check",
                "Unit tests",
                "Repository contract",
                "SonarQube Cloud",
            ],
            names,
        )

    def test_unit_tests_install_the_requirements_before_the_suite(self) -> None:
        steps = self.unit["steps"]
        setup = step_index(steps, lambda s: "setup-python" in str(s.get("uses", "")))
        suite = step_index(steps, lambda s: SUITE_MARKER in str(s.get("run", "")))
        self.assertNotIn("unittest discover", str(steps[suite].get("run", "")))
        for requirement in REQUIREMENT_FILES:
            with self.subTest(requirement=requirement):
                install = step_index(
                    steps, lambda s, r=requirement: f"-r {r}" in str(s.get("run", ""))
                )
                self.assertLess(setup, install)
                self.assertLess(install, suite)

    def test_unit_tests_install_what_the_lint_job_installs(self) -> None:
        def installs(job: dict[str, Any]) -> set[str]:
            return {
                requirement
                for step in job["steps"]
                for requirement in REQUIREMENT_FILES
                if f"-r {requirement}" in str(step.get("run", ""))
            }

        self.assertEqual(set(REQUIREMENT_FILES), installs(self.lint))
        self.assertEqual(installs(self.lint), installs(self.unit))

    def test_suite_runs_the_explicit_census_modules(self) -> None:
        matches = [
            str(step["run"])
            for step in self.unit["steps"]
            if SUITE_MARKER in str(step.get("run", ""))
        ]
        self.assertEqual(1, len(matches))
        run = matches[0]
        pre, post = run.split(UNITTEST_COMMAND, 1)
        self.assertEqual(list(UNIT_TEST_MODULES), listed_modules(run))
        self.assertNotIn("unittest discover", run)
        self.assertNotIn("make test", run)
        self.assertNotIn("python3 -m unittest", run)
        self.assertIn("missing listed test module", pre)
        self.assertIn("unit test collection is zero", pre)
        self.assertIn("unit test collection is zero", post)
        self.assertIn("set -euo pipefail", run)
        self.assertNotIn("|| true", run)
        self.assertNotIn("continue-on-error", self.unit)
        for step in self.unit["steps"]:
            self.assertNotIn("continue-on-error", step)

    def test_every_discovered_module_is_classified_once(self) -> None:
        """A new tests/**/test_*.py file fails this check until it is listed.

        PR lanes are Repo.mk ``REPO_TEST_MODULES`` and the Unit tests job.
        ``NIGHTLY_ONLY_MODULES`` is the only other bucket, and it is empty:
        security, governance, SDK, release, and false-green witnesses stay on
        a pull request. Nightly ``test-deep`` still reruns the full tree.
        """
        unit = listed_modules(
            next(
                str(step["run"])
                for step in self.unit["steps"]
                if SUITE_MARKER in str(step.get("run", ""))
            )
        )
        repo_text = (ROOT / "Repo.mk").read_text(encoding="utf-8")
        repo_block = repo_text.split("REPO_TEST_MODULES :=", 1)[1].split(
            "repo-test:", 1
        )[0]
        repo = re.findall(r"tests/\S+\.py", repo_block)
        nightly: list[str] = []
        discovered = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "tests").rglob("test_*.py")
            if path.is_file()
        )
        classified = unit + repo + nightly
        self.assertEqual(sorted(classified), discovered)
        self.assertEqual(len(classified), len(set(classified)))
        self.assertIn("tests/workflows/test_self_sonar.py", unit)
        self.assertEqual([], nightly)


class RepositoryContractJobTests(unittest.TestCase):
    """Defect B: Core's make-v1 contract runs at the event revision."""

    def setUp(self) -> None:
        self.workflow = load(SELF_CI)
        self.job = job_named(self.workflow, "Repository contract")
        self.steps = self.job["steps"]

    def test_job_is_triggered_for_pull_requests(self) -> None:
        triggers = self.workflow.get(True, self.workflow.get("on"))
        self.assertIn("pull_request", triggers)

    def test_checks_out_the_immutable_event_revision(self) -> None:
        checkout = self.steps[0]
        self.assertEqual("Checkout immutable event revision", checkout["name"])
        self.assertEqual("${{ github.sha }}", checkout["env"]["REVISION"])
        self.assertIn('fetch --depth=1 origin "${REVISION}"', checkout["run"])

    def test_uses_the_same_pinned_python(self) -> None:
        lint = job_named(self.workflow, "Lint and Type Check")
        self.assertEqual(
            setup_python(lint["steps"])["uses"], setup_python(self.steps)["uses"]
        )
        self.assertEqual("3.12", setup_python(self.steps)["with"]["python-version"])

    def test_runs_every_declared_phase_in_order_one_step_each(self) -> None:
        phases = json.loads(REPO_WORKFLOW.read_text(encoding="utf-8"))[
            "required_phases"
        ]
        self.assertEqual(["setup", "validate", "check", "test"], phases)
        # The Core self-pin ancestry guard (AGENTS.md §34) runs before setup;
        # it is a gate on the workflows, not a repository phase.
        runs = [
            str(step["run"]).strip()
            for step in self.steps
            if "run" in step
            and step.get("name") != "Core self-pins are ancestors of main"
        ][1:]
        makes = [run for run in runs if run.startswith("make ")]
        self.assertEqual([f"make {phase}" for phase in phases], makes)
        # The only other steps measure `make test` for Core's Sonar analysis:
        # install the hash-pinned coverage after setup, write the report after
        # the test phase. Neither replaces or repeats a phase.
        others = [run for run in runs if not run.startswith("make ")]
        self.assertEqual(2, len(others))
        self.assertLess(runs.index("make setup"), runs.index(others[0]))
        self.assertLess(runs.index(others[0]), runs.index("make validate"))
        self.assertIn("--require-hashes", others[0])
        self.assertIn("-r requirements-coverage.txt", others[0])
        self.assertLess(runs.index("make test"), runs.index(others[1]))
        self.assertIn("python -m coverage combine", others[1])
        self.assertNotIn("unittest", others[0] + others[1])

    def test_no_step_tolerates_failure(self) -> None:
        for step in self.steps:
            with self.subTest(step=step.get("name")):
                self.assertNotIn("continue-on-error", step)
        self.assertNotIn("continue-on-error", self.job)


class RulesetContextTests(unittest.TestCase):
    """Defect C: every required context is exact, unique and produced."""

    def setUp(self) -> None:
        payload = json.loads(RULESET.read_text(encoding="utf-8"))
        rules = [r for r in payload["rules"] if r["type"] == "required_status_checks"]
        self.assertEqual(1, len(rules))
        self.checks = rules[0]["parameters"]["required_status_checks"]
        self.contexts = [check["context"] for check in self.checks]

    def test_required_contexts_are_exact(self) -> None:
        self.assertEqual(EXPECTED_CONTEXTS, self.contexts)

    def test_bare_validate_is_not_required(self) -> None:
        self.assertNotIn("validate", self.contexts)

    def test_every_context_is_github_actions(self) -> None:
        for check in self.checks:
            with self.subTest(context=check["context"]):
                self.assertEqual(15368, check["integration_id"])

    def test_governance_job_has_a_unique_check_run_name(self) -> None:
        self.assertEqual(
            "Governance validate", check_run_name(load(GOVERNANCE_CI), "validate")
        )

    def test_every_context_is_produced_by_exactly_one_job(self) -> None:
        produced: dict[str, int] = {}
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            workflow = load(path)
            for job_id, job in (workflow.get("jobs") or {}).items():
                caller = check_run_name(workflow, job_id)
                names = {caller}
                uses = str(job.get("uses", ""))
                if uses.startswith("./.github/workflows/"):
                    called = load(ROOT / uses[2:])
                    names = {
                        f"{caller} / {check_run_name(called, called_id)}"
                        for called_id in called["jobs"]
                    }
                for name in names:
                    produced[name] = produced.get(name, 0) + 1
        for context in self.contexts:
            with self.subTest(context=context):
                self.assertEqual(1, produced.get(context, 0), context)


class NightlyDeepWorkflowTests(unittest.TestCase):
    """Full discovery is the main-only nightly workflow, not a self-ci job."""

    def setUp(self) -> None:
        self.workflow = load(NIGHTLY)
        self.job = job_named(self.workflow, "Nightly deep tests")
        self.steps = self.job["steps"]

    def test_schedule_is_0717_utc_and_dispatch_is_allowed(self) -> None:
        triggers = triggers_of(self.workflow)
        self.assertEqual([{"cron": "17 7 * * *"}], triggers["schedule"])
        self.assertIn("workflow_dispatch", triggers)
        self.assertNotIn("pull_request", triggers)
        self.assertNotIn("push", triggers)
        text = NIGHTLY.read_text(encoding="utf-8")
        self.assertNotIn("pull_request:", text)
        self.assertEqual("nightly-deep.yml", NIGHTLY.name)

    def test_permissions_are_contents_read(self) -> None:
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])

    def test_ref_guard_fails_closed_unless_default_branch_is_main(self) -> None:
        guard = self.steps[0]
        self.assertEqual("Require Core default branch main", guard["name"])
        self.assertEqual("${{ github.repository }}", guard["env"]["REPOSITORY"])
        self.assertEqual("${{ github.ref_name }}", guard["env"]["REF_NAME"])
        self.assertEqual(
            "${{ github.event.repository.default_branch }}",
            guard["env"]["DEFAULT_BRANCH"],
        )
        run = str(guard["run"])
        self.assertIn("Quantum-L9/l9-ci-core", run)
        self.assertIn('"${REF_NAME}" != "${DEFAULT_BRANCH}"', run)
        self.assertIn('"${DEFAULT_BRANCH}" != "main"', run)
        self.assertIn("exit 1", run)
        self.assertIn("set -euo pipefail", run)

    def test_checks_out_the_exact_run_commit(self) -> None:
        checkout_at = step_index(
            self.steps,
            lambda step: step.get("name") == "Checkout immutable event revision",
        )
        reject_at = step_index(
            self.steps,
            lambda step: step.get("name") == "Reject an unadmitted dispatch",
        )
        self.assertLess(reject_at, checkout_at)
        checkout = self.steps[checkout_at]
        self.assertEqual("Checkout immutable event revision", checkout["name"])
        self.assertEqual("${{ github.sha }}", checkout["env"]["REVISION"])
        self.assertIn('fetch --depth=1 origin "${REVISION}"', checkout["run"])
        self.assertIn('test "$(git rev-parse HEAD)" = "${REVISION}"', checkout["run"])

    def test_runs_make_test_deep_fail_closed_on_the_pinned_interpreter(self) -> None:
        lint = job_named(load(SELF_CI), "Lint and Type Check")
        nightly_python = setup_python(self.steps)
        self.assertEqual(setup_python(lint["steps"])["uses"], nightly_python["uses"])
        self.assertEqual("3.12", nightly_python["with"]["python-version"])
        self.assertGreaterEqual(self.job["timeout-minutes"], 10)
        runs = [str(step.get("run", "")) for step in self.steps]
        self.assertIn("make setup", runs[-2])
        self.assertIn("make test-deep", runs[-1])
        self.assertNotIn("unittest discover", runs[-1])
        self.assertNotIn("continue-on-error", self.job)
        for step in self.steps:
            self.assertNotIn("continue-on-error", step)
            if "run" in step:
                self.assertIn("set -euo pipefail", step["run"])
                self.assertNotIn("|| true", step["run"])
        self.assertNotIn("unittest discover", NIGHTLY.read_text(encoding="utf-8"))
        self_ci = SELF_CI.read_text(encoding="utf-8")
        self.assertNotIn("make test-deep", self_ci)
        self.assertNotIn("nightly-deep.yml", self_ci)
        self.assertNotIn("17 7 * * *", self_ci)


if __name__ == "__main__":
    unittest.main()
