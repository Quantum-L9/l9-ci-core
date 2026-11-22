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


def _run(
    script: str,
    env: dict[str, str],
    stubs: dict[str, int],
    cwd: Path | None = None,
) -> tuple[int, str]:
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
            cwd=cwd,
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
        "GITLEAKS_OUTCOME": "executed_pass",
        "PIP_AUDIT_STEP": "success",
        "PIP_AUDIT_OUTCOME": "executed_pass",
        "BANDIT_STEP": "success",
        "BANDIT_OUTCOME": "executed_no_findings",
        "NPM_AUDIT_STEP": "skipped",
        "NPM_AUDIT_OUTCOME": "",
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
        step = _step(REPORT_STEP)
        self.assertEqual("always()", step["if"])
        self.assertEqual(REPORT_STEP, _steps()[-1]["name"])
        # The job default is the caller's working-directory; if that path is
        # missing, a step that inherits it cannot start and reports nothing.
        self.assertEqual("${{ github.workspace }}", step["working-directory"])

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
                {
                    "GITLEAKS_STEP": "failure",
                    "GITLEAKS_OUTCOME": "executed_fail",
                    "PIP_AUDIT_STEP": "skipped",
                    "PIP_AUDIT_OUTCOME": "",
                },
                "pip-audit",
                "not_executed",
            ),
            "gitleaks found a secret": (
                {"GITLEAKS_STEP": "failure", "GITLEAKS_OUTCOME": "executed_fail"},
                "gitleaks",
                "executed_fail",
            ),
            "gitleaks download failed before the scan": (
                {"GITLEAKS_STEP": "failure", "GITLEAKS_OUTCOME": ""},
                "gitleaks",
                "not_executed (the step failed before",
            ),
            "pip-audit install failed before the scan": (
                {"PIP_AUDIT_STEP": "failure", "PIP_AUDIT_OUTCOME": ""},
                "pip-audit",
                "not_executed (the step failed before",
            ),
            "npm audit refused a missing lockfile": (
                {
                    "NPM_AUDIT_REQUESTED": "true",
                    "NODE_PROJECT": "true",
                    "NPM_AUDIT_STEP": "failure",
                    "NPM_AUDIT_OUTCOME": "",
                },
                "npm audit",
                "not_executed (the step failed before",
            ),
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
                    "NPM_AUDIT_OUTCOME": "executed_fail",
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
            NPM_AUDIT_REQUESTED="true",
            NODE_PROJECT="true",
            NPM_AUDIT_STEP="success",
            NPM_AUDIT_OUTCOME="executed_pass",
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


class RequiredScanOutcomeTests(unittest.TestCase):
    """A required scan states its outcome only once its scanner has run."""

    def test_npm_audit_states_an_outcome_only_after_the_lockfile_check(self) -> None:
        script = str(_step("Node dependency audit (npm audit)")["run"])
        with tempfile.TemporaryDirectory() as temp:
            code, text = _run(f"cd {temp}\n{script}", {}, {"npm": 0})
            self.assertEqual(1, code)
            self.assertNotIn("outcome=", text)
            (Path(temp) / "package-lock.json").write_text("{}", encoding="utf-8")
            code, text = _run(f"cd {temp}\n{script}", {}, {"npm": 0})
            self.assertEqual(0, code)
            self.assertIn("outcome=executed_pass", text)
            code, text = _run(f"cd {temp}\n{script}", {}, {"npm": 1})
            self.assertEqual(1, code, "npm audit stays required when requested")
            self.assertIn("outcome=executed_fail", text)

    def test_pip_audit_keeps_its_exit_code_and_states_its_outcome(self) -> None:
        script = str(_step("Python dependency audit (pip-audit)")["run"])
        env = {"L9_CI_INSTALL_COMMAND": ""}
        with tempfile.TemporaryDirectory() as project:
            (Path(project) / "requirements.txt").write_text("x==1\n", "utf-8")
            cwd = Path(project)
            code, text = _run(script, env, {"python": 0, "pip": 0, "pip-audit": 0}, cwd)
            self.assertEqual(0, code)
            self.assertIn("outcome=executed_pass", text)
            code, text = _run(script, env, {"python": 0, "pip": 0, "pip-audit": 1}, cwd)
            self.assertEqual(1, code, "pip-audit stays required")
            self.assertIn("outcome=executed_fail", text)
            code, text = _run(script, env, {"python": 0, "pip": 1, "pip-audit": 0}, cwd)
            self.assertNotEqual(0, code)
            self.assertNotIn("outcome=", text)

    def test_gitleaks_states_an_outcome_only_after_acquisition(self) -> None:
        script = str(_step("Secret scan (gitleaks)")["run"])
        acquisition, scan = script.split("tar -xzf", 1)
        self.assertNotIn("outcome=", acquisition)
        self.assertIn("outcome=executed_pass", scan)
        self.assertIn("outcome=executed_fail", scan)
        self.assertIn('exit "${status}"', scan)

    def test_gitleaks_is_downloaded_over_https_only(self) -> None:
        # Every request, redirects included, is HTTPS with TLS 1.2 or later;
        # the checksum then pins the bytes.
        script = str(_step("Secret scan (gitleaks)")["run"])
        # Two downloads: gitleaks' default rules at the pinned version (their
        # ids are the ones a repository rule must not reuse) and the binary.
        downloads = [line for line in script.splitlines() if "curl " in line]
        self.assertEqual(2, len(downloads))
        for download in downloads:
            self.assertIn("--proto '=https'", download)
            self.assertIn("--tlsv1.2", download)
        self.assertIn("GITLEAKS_SHA256", script)
        self.assertIn("GITLEAKS_DEFAULTS_SHA256", script)
