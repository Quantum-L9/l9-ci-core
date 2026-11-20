"""Core's own SonarQube Cloud project is analysed, and the scan is not a gate.

The project Quantum-L9_l9-ci-core existed with no analysis. The scan uses the
organization SONAR_TOKEN. It stays off the required-check set, does not wait
on the quality gate, and does not run for a fork pull request.

It imports coverage measured on the test runs self-ci.yml already makes, so a
pull request that adds tested code no longer reads as 0% covered. The token
reaches the Sonar job only, and that job runs no repository code.
"""

from __future__ import annotations

import configparser
import json
import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "self-sonar.yml"
PROPERTIES = ROOT / "sonar-project.properties"
RULESET = ROOT / "docs" / "release" / "rulesets" / "core-main-protection.json"
SELF_CI = ROOT / ".github" / "workflows" / "self-ci.yml"
COVERAGE_RC = ROOT / ".coveragerc"
COVERAGE_REQUIREMENTS = ROOT / "requirements-coverage.txt"
REPO_MK = ROOT / "Repo.mk"
REPORTS = {
    "Unit tests": "coverage-unit-tests",
    "Repository contract": "coverage-repository-contract",
}
DOWNLOAD_ACTION = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def job_named(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [job for job in workflow["jobs"].values() if job.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


SCAN_ACTION = (
    "SonarSource/sonarqube-scan-action@59202af334b4ba7b5db01b701b8e79f8b3bef0f4"
)
CHECKOUT_ACTION = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
SCANNER_VERSION = "8.4.0.16527"


class SelfSonarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_scan_action_and_checkout_are_full_shas(self) -> None:
        self.assertIn(f"uses: {SCAN_ACTION} # v8.5.0", self.text)
        self.assertIn(f"uses: {CHECKOUT_ACTION} # v4.2.0", self.text)
        self.assertIn(f"scannerVersion: {SCANNER_VERSION}", self.text)
        for reference in re.findall(r"uses:\s*(\S+)", self.text):
            self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")

    def test_token_is_the_named_organization_secret(self) -> None:
        self.assertIn("SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}", self.text)
        self.assertNotIn("secrets: inherit", self.text)

    def test_scan_is_not_a_quality_gate_and_skips_forks(self) -> None:
        properties = PROPERTIES.read_text(encoding="utf-8")
        self.assertNotIn("qualitygate.wait", self.text)
        self.assertNotIn("sonar.qualitygate.wait", properties)
        self.assertIn("github.event_name != 'pull_request'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )

    def test_permissions_are_contents_read(self) -> None:
        self.assertRegex(self.text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))
        self.assertIsNone(re.search(r":\s+write\s*$", self.text, re.MULTILINE))

    def test_properties_name_the_existing_project(self) -> None:
        properties = PROPERTIES.read_text(encoding="utf-8")
        self.assertIn("sonar.organization=quantum-l9", properties)
        self.assertIn("sonar.projectKey=Quantum-L9_l9-ci-core", properties)
        self.assertIn("sonar.python.version=3.12", properties)
        self.assertIn("sonar.sources=.", properties)
        self.assertIn("sonar.tests=tests", properties)
        exclusions = next(
            line.split("=", 1)[1]
            for line in properties.splitlines()
            if line.startswith("sonar.exclusions=")
        )
        self.assertIn("tests/**", {item.strip() for item in exclusions.split(",")})

    def test_ruleset_does_not_require_the_sonar_check(self) -> None:
        document = json.loads(RULESET.read_text(encoding="utf-8"))
        contexts = [
            check["context"]
            for rule in document["rules"]
            if rule["type"] == "required_status_checks"
            for check in rule["parameters"]["required_status_checks"]
        ]
        self.assertTrue(contexts)
        self.assertFalse(any("sonar" in context.lower() for context in contexts))


if __name__ == "__main__":
    unittest.main()


class SonarCoverageTests(unittest.TestCase):
    """Coverage comes from the runs Core already makes, and only Sonar has the token."""

    def setUp(self) -> None:
        self.self_ci = load(SELF_CI)
        self.self_ci_text = SELF_CI.read_text(encoding="utf-8")
        self.sonar = load(WORKFLOW)

    def test_sonar_is_called_after_both_test_jobs_with_the_named_token(self) -> None:
        caller = job_named(self.self_ci, "SonarQube Cloud")
        self.assertEqual("./.github/workflows/self-sonar.yml", caller["uses"])
        self.assertEqual(["validate", "repository-contract"], caller["needs"])
        self.assertEqual(
            {"SONAR_TOKEN": "${{ secrets.SONAR_TOKEN }}"}, caller["secrets"]
        )
        self.assertEqual({"contents": "read"}, caller["permissions"])
        self.assertIn("cancelled()", str(caller["if"]))
        triggers = self.sonar.get(True, self.sonar.get("on"))
        self.assertEqual(["workflow_call"], list(triggers))
        self.assertIn("SONAR_TOKEN", triggers["workflow_call"]["secrets"])

    def test_the_token_reaches_no_other_job(self) -> None:
        for job_id, job in self.self_ci["jobs"].items():
            if job.get("name") == "SonarQube Cloud":
                continue
            with self.subTest(job=job_id):
                self.assertNotIn("SONAR", yaml.safe_dump(job))
        self.assertEqual(1, self.self_ci_text.count("secrets.SONAR_TOKEN"))

    def test_sonar_job_runs_no_repository_code(self) -> None:
        steps = self.sonar["jobs"]["sonar"]["steps"]
        runs = [str(step["run"]) for step in steps if "run" in step]
        self.assertEqual(1, len(runs))
        self.assertNotRegex(runs[0], r"\b(python|make|bash \S+\.sh|pip)\b")
        uses = [str(step["uses"]) for step in steps if "uses" in step]
        self.assertIn(
            f"{DOWNLOAD_ACTION} # v4.3.0", WORKFLOW.read_text(encoding="utf-8")
        )
        self.assertEqual(3, len(uses))

    def test_each_test_job_measures_its_own_run_and_uploads_one_report(self) -> None:
        for job_name, artifact in REPORTS.items():
            with self.subTest(job=job_name):
                job = job_named(self.self_ci, job_name)
                uploads = [
                    step
                    for step in job["steps"]
                    if "upload-artifact" in str(step.get("uses", ""))
                ]
                self.assertEqual(1, len(uploads))
                self.assertEqual(artifact, uploads[0]["with"]["name"])
                self.assertEqual(
                    f"coverage-reports/{artifact}.xml", uploads[0]["with"]["path"]
                )
                self.assertEqual("error", uploads[0]["with"]["if-no-files-found"])
                self.assertNotIn("secrets.", yaml.safe_dump(job))
                runs = "\n".join(str(s.get("run", "")) for s in job["steps"])
                self.assertIn("--require-hashes", runs)
                self.assertIn(f"-o coverage-reports/{artifact}.xml", runs)
                self.assertNotIn("unittest discover", runs)

    def test_repository_contract_measures_the_make_test_run(self) -> None:
        job = job_named(self.self_ci, "Repository contract")
        test_step = next(s for s in job["steps"] if s.get("run") == "make test")
        self.assertEqual(
            "python -m coverage run --parallel-mode -m unittest",
            test_step["env"]["REPO_TEST_RUNNER"],
        )
        repo_mk = REPO_MK.read_text(encoding="utf-8")
        self.assertIn("REPO_TEST_RUNNER ?= $(PYTHON) -m unittest\n", repo_mk)
        self.assertIn(
            "\t@$(REPO_TEST_RUNNER) --verbose $(REPO_TEST_MODULES)\n", repo_mk
        )

    def test_properties_import_exactly_the_two_reports(self) -> None:
        properties = PROPERTIES.read_text(encoding="utf-8")
        paths = next(
            line.split("=", 1)[1]
            for line in properties.splitlines()
            if line.startswith("sonar.python.coverage.reportPaths=")
        )
        self.assertEqual(
            sorted(f"coverage-reports/{name}.xml" for name in REPORTS.values()),
            sorted(paths.split(",")),
        )
        required = next(
            str(step["run"])
            for step in self.sonar["jobs"]["sonar"]["steps"]
            if "run" in step
        )
        for name in REPORTS.values():
            self.assertIn(f"{name}.xml", required)

    def test_coverage_is_hash_pinned_and_paths_are_root_relative(self) -> None:
        requirements = COVERAGE_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^coverage==\d+\.\d+\.\d+ \\$")
        self.assertRegex(requirements, r"--hash=sha256:[0-9a-f]{64}")
        config = configparser.ConfigParser()
        config.read(COVERAGE_RC, encoding="utf-8")
        self.assertEqual("True", config["run"]["relative_files"])
        self.assertEqual(".", config["run"]["source"].strip())
        self.assertIn("tests/*", config["run"]["omit"].split())
