"""T04: an advisory, skipped or failed scan in security.yml never reads as run.

The Bandit and report steps are executed here with stub tools, so the outcome
labels are observed, not inferred from the YAML text. Nothing is promoted:
Bandit stays advisory and npm audit stays off unless a caller requests it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / ".github" / "workflows" / "security.yml"
BANDIT_STEP = "Python static security scan (bandit)"
REPORT_STEP = "Report scan requiredness and outcomes"


def _steps() -> list[dict[str, Any]]:
    document = yaml.safe_load(SECURITY.read_text(encoding="utf-8"))
    return document["jobs"]["security"]["steps"]


def _step(name: str) -> dict[str, Any]:
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _run(script: str, env: dict[str, str], stubs: dict[str, int]) -> tuple[int, str]:
    """Run a step script with stub executables; return (exit code, all output)."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, code in stubs.items():
            stub = bin_dir / name
            stub.write_text(f"#!/usr/bin/env bash\nexit {code}\n", encoding="utf-8")
            stub.chmod(0o755)
        output = root / "output"
        summary = root / "summary"
        output.touch()
        summary.touch()
        completed = subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        text = "\n".join(
            (
                completed.stdout,
                completed.stderr,
                output.read_text(encoding="utf-8"),
                summary.read_text(encoding="utf-8"),
            )
        )
        return completed.returncode, text


class BanditOutcomeTests(unittest.TestCase):
    def run_bandit(self, exit_code: int) -> tuple[int, str]:
        return _run(
            str(_step(BANDIT_STEP)["run"]),
            {"SOURCE_DIR": "."},
            {"pip": 0, "bandit": exit_code},
        )

    def test_known_good_clean_scan_is_labelled_executed(self) -> None:
        code, text = self.run_bandit(0)
        self.assertEqual(0, code)
        self.assertIn("outcome=executed_no_findings", text)
        self.assertNotIn("::warning::", text)

    def test_findings_stay_advisory_and_are_labelled_as_findings(self) -> None:
        code, text = self.run_bandit(1)
        self.assertEqual(0, code, "Bandit is advisory; findings must not block")
        self.assertIn("outcome=executed_findings", text)
        self.assertIn("::warning::Bandit (advisory) reported findings", text)

    def test_known_bad_scanner_error_is_not_labelled_executed(self) -> None:
        for exit_code in (2, 127):
            with self.subTest(exit_code=exit_code):
                code, text = self.run_bandit(exit_code)
                self.assertEqual(0, code, "nothing is promoted to blocking")
                self.assertIn("outcome=not_executed", text)
                self.assertIn("did not complete", text)
                self.assertNotIn("executed_no_findings", text)
                self.assertNotIn("executed_findings", text)

    def test_the_masking_fallback_is_gone(self) -> None:
        script = str(_step(BANDIT_STEP)["run"])
        self.assertNotIn("|| echo", script)
        self.assertNotIn("non-blocking", script)
        self.assertEqual("bandit", _step(BANDIT_STEP)["id"])


class ScanReportTests(unittest.TestCase):
    BASE = {
        "DETECT_OUTCOME": "success",
        "PYTHON_PROJECT": "true",
        "NODE_PROJECT": "false",
        "NPM_AUDIT_REQUESTED": "false",
        "GITLEAKS_STEP": "success",
        "PIP_AUDIT_STEP": "success",
        "BANDIT_STEP": "success",
        "BANDIT_OUTCOME": "executed_no_findings",
        "NPM_AUDIT_STEP": "skipped",
    }

    def rows(self, **changes: str) -> dict[str, tuple[str, str]]:
        code, text = _run(str(_step(REPORT_STEP)["run"]), {**self.BASE, **changes}, {})
        self.assertEqual(0, code, text)
        table: dict[str, tuple[str, str]] = {}
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if line.startswith("| ") and len(cells) == 3 and cells[0] != "Scan":
                table[cells[0]] = (cells[1], cells[2])
        return table

    def test_report_runs_even_when_an_earlier_step_failed(self) -> None:
        self.assertEqual("always()", _step(REPORT_STEP)["if"])
        self.assertEqual(REPORT_STEP, _steps()[-1]["name"])

    def test_known_good_python_project(self) -> None:
        rows = self.rows()
        self.assertEqual(("required", "executed_pass"), rows["gitleaks"])
        self.assertEqual("executed_pass", rows["pip-audit"][1])
        self.assertEqual(("advisory", "executed_no_findings"), rows["bandit"])
        self.assertEqual(
            ("not run", "not_run (run-npm-audit is false)"), rows["npm audit"]
        )

    def test_known_bad_skipped_or_failed_scans_never_read_as_passed(self) -> None:
        cases: dict[str, tuple[dict[str, str], str, str]] = {
            "bandit errored": ({"BANDIT_OUTCOME": "not_executed"}, "bandit", "not_exe"),
            "bandit step never ran": (
                {"BANDIT_OUTCOME": "", "BANDIT_STEP": "skipped"},
                "bandit",
                "not_executed",
            ),
            "pip-audit skipped after gitleaks failed": (
                {"GITLEAKS_STEP": "failure", "PIP_AUDIT_STEP": "skipped"},
                "pip-audit",
                "not_executed",
            ),
            "gitleaks failed": ({"GITLEAKS_STEP": "failure"}, "gitleaks", "executed_f"),
            "no python project": (
                {"PYTHON_PROJECT": "false", "BANDIT_OUTCOME": ""},
                "bandit",
                "not_applicable",
            ),
            "npm audit requested without node": (
                {"NPM_AUDIT_REQUESTED": "true"},
                "npm audit",
                "not_applicable",
            ),
            "npm audit requested and failed": (
                {
                    "NPM_AUDIT_REQUESTED": "true",
                    "NODE_PROJECT": "true",
                    "NPM_AUDIT_STEP": "failure",
                },
                "npm audit",
                "executed_fail",
            ),
            "detection failed": (
                {"DETECT_OUTCOME": "failure", "PYTHON_PROJECT": ""},
                "pip-audit",
                "not_executed",
            ),
        }
        for name, (changes, scan, expected) in cases.items():
            with self.subTest(case=name):
                outcome = self.rows(**changes)[scan][1]
                self.assertTrue(outcome.startswith(expected), outcome)
                self.assertNotIn("pass", outcome)
                self.assertNotIn("no_findings", outcome)

    def test_requested_npm_audit_is_required_and_reported(self) -> None:
        rows = self.rows(
            NPM_AUDIT_REQUESTED="true", NODE_PROJECT="true", NPM_AUDIT_STEP="success"
        )
        self.assertEqual(("required", "executed_pass"), rows["npm audit"])

    def test_nothing_is_promoted(self) -> None:
        document = yaml.safe_load(SECURITY.read_text(encoding="utf-8"))
        inputs = document[True]["workflow_call"]["inputs"]
        self.assertIs(False, inputs["run-npm-audit"]["default"])
        for step in _steps():
            self.assertNotIn("continue-on-error", step)


if __name__ == "__main__":
    unittest.main()
