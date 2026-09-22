"""Contract tests for the legacy reusable-workflow compatibility inputs."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
PR_PIPELINE = ROOT / ".github" / "workflows" / "pr-pipeline.yml"
SECURITY = ROOT / ".github" / "workflows" / "security.yml"


def _document(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise AssertionError(f"{path} must contain a YAML mapping")
    return loaded


def _call_inputs(path: Path) -> dict[str, Any]:
    document = _document(path)
    # YAML 1.1 parses the unquoted GitHub Actions key `on` as boolean true.
    trigger = document.get("on", document.get(True))
    return trigger["workflow_call"]["inputs"]


def _job(path: Path, name: str) -> dict[str, Any]:
    return _document(path)["jobs"][name]


class CompatibilityInputTests(unittest.TestCase):
    def test_pr_pipeline_rejects_unsupported_non_neutral_legacy_inputs(self) -> None:
        inputs = _call_inputs(PR_PIPELINE)
        expected_neutral = {
            "enable-pydantic-strict": False,
            "changed-files": "",
            "pr-labels": "",
            "labels-known": False,
        }
        for name, neutral in expected_neutral.items():
            with self.subTest(input=name):
                self.assertEqual(neutral, inputs[name]["default"])
                self.assertIn("fails explicitly", inputs[name].get("description", ""))

        preflight = _job(PR_PIPELINE, "validate-compatibility-inputs")
        script = preflight["steps"][0]["run"]
        self.assertIn("exit 1", script)
        self.assertIn("Unsupported pr-pipeline compatibility input", script)
        for value in (
            "enable-pydantic-strict=true",
            "changed-files=<non-empty>",
            "pr-labels=<non-empty>",
            "labels-known=true",
        ):
            with self.subTest(value=value):
                self.assertIn(value, script)

        jobs = _document(PR_PIPELINE)["jobs"]
        self.assertEqual("validate-compatibility-inputs", jobs["validate"]["needs"])
        self.assertEqual("validate-compatibility-inputs", jobs["security"]["needs"])

    def test_pr_pipeline_run_security_controls_real_security_work(self) -> None:
        run_security = _call_inputs(PR_PIPELINE)["run-security"]
        self.assertEqual("boolean", run_security["type"])
        self.assertIs(False, run_security["default"])

        security_job = _job(PR_PIPELINE, "security")
        self.assertEqual("inputs.run-security", security_job["if"])
        self.assertEqual("./.github/workflows/security.yml", security_job["uses"])
        self.assertEqual(
            "${{ inputs.run-security }}",
            security_job["with"]["run-npm-audit"],
        )
        self.assertEqual(
            "${{ inputs.working-directory }}",
            security_job["with"]["working-directory"],
        )

    def test_security_run_npm_audit_false_never_auto_enables_from_lockfile(
        self,
    ) -> None:
        run_npm_audit = _call_inputs(SECURITY)["run-npm-audit"]
        self.assertEqual("boolean", run_npm_audit["type"])
        self.assertIs(False, run_npm_audit["default"])

        audit = next(
            step
            for step in _job(SECURITY, "security")["steps"]
            if step.get("name") == "Node dependency audit (npm audit)"
        )
        self.assertEqual(
            "steps.detect.outputs.node == 'true' && inputs.run-npm-audit",
            audit["if"],
        )
        self.assertNotIn("hashFiles(", audit["if"])

    def test_security_run_npm_audit_true_runs_or_fails_explicitly(self) -> None:
        audit = next(
            step
            for step in _job(SECURITY, "security")["steps"]
            if step.get("name") == "Node dependency audit (npm audit)"
        )
        script = audit["run"]
        self.assertRegex(script, r'if \[ ! -f "package-lock\.json" \]; then')
        self.assertIn("run-npm-audit=true requires package-lock.json", script)
        self.assertIn("exit 1", script)
        self.assertIn("npm audit --audit-level=high", script)
        self.assertNotIn("skipping npm audit", script)

    def test_security_honors_forwarded_working_directory(self) -> None:
        inputs = _call_inputs(SECURITY)
        self.assertEqual(".", inputs["working-directory"]["default"])
        security_job = _job(SECURITY, "security")
        self.assertEqual(
            "${{ inputs.working-directory }}",
            security_job["defaults"]["run"]["working-directory"],
        )

    def test_compatibility_workflows_do_not_add_sdk_semantics(self) -> None:
        combined = PR_PIPELINE.read_text(encoding="utf-8") + SECURITY.read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "finding-bundle",
            "canonical finding",
            "bundle validate",
            "gate evaluate",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
