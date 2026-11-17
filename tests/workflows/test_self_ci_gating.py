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
"""

from __future__ import annotations

import json
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

    def test_unit_tests_install_the_requirements_before_the_suite(self) -> None:
        steps = self.unit["steps"]
        setup = step_index(steps, lambda s: "setup-python" in str(s.get("uses", "")))
        suite = step_index(
            steps, lambda s: "unittest discover" in str(s.get("run", ""))
        )
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

    def test_suite_runs_on_the_set_up_interpreter_over_the_whole_tree(self) -> None:
        run = next(
            str(step["run"])
            for step in self.unit["steps"]
            if "unittest discover" in str(step.get("run", ""))
        )
        self.assertIn("python -m unittest discover", run)
        self.assertNotIn("python3 -m unittest", run)
        self.assertIn("--start-directory tests", run)
        self.assertIn("--pattern 'test_*.py'", run)


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
        runs = [str(step["run"]).strip() for step in self.steps if "run" in step][1:]
        self.assertEqual([f"make {phase}" for phase in phases], runs)

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


if __name__ == "__main__":
    unittest.main()
