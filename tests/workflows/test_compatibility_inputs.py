"""Contract tests for the retained ``security.yml`` reusable workflow inputs.

The v1 ``pr-pipeline.yml`` shim that once called this workflow was retired on
2026-10-22 (``AGENTS.md`` §15). ``security.yml`` stays because Core's own
``self-security.yml`` dogfoods it; its inputs keep the explicit, fail-closed
semantics below.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SECURITY = WORKFLOWS / "security.yml"


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
    def test_v1_pr_pipeline_shim_is_retired(self) -> None:
        self.assertFalse((WORKFLOWS / "pr-pipeline.yml").exists())

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

    def test_security_workflow_does_not_add_sdk_semantics(self) -> None:
        combined = SECURITY.read_text(encoding="utf-8")
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
