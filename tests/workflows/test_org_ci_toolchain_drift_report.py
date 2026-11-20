"""Toolchain drift is reported, never enforced (L2-closeout item 2).

Operator decision 2026-11-20: only checks that protect working or secure code
block a merge; toolchain-version drift is housekeeping. The alignment action
writes ``.l9/runtime/toolchain-alignment.json``; org-ci.yml's "Report
toolchain alignment drift (advisory)" step turns each drifting declaration
into a ``file:line`` warning and a summary line, and never fails the job.
These tests execute that embedded step against each kind of report.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
ENFORCE = "Report toolchain alignment drift (advisory)"


def steps() -> list[dict[str, object]]:
    document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    return document["jobs"]["analyze"]["steps"]


def step(name: str) -> dict[str, object]:
    [found] = [s for s in steps() if s.get("name") == name]
    return found


def enforcement_source() -> str:
    run = str(step(ENFORCE)["run"])
    start = run.index("<<'PY'\n") + len("<<'PY'\n")
    return textwrap.dedent(run[start : run.index("\nPY", start)]) + "\n"


def finding(cls: str, tool: str = "ruff") -> dict[str, object]:
    return {
        "class": cls,
        "declared": "0.0.1",
        "line": 7,
        "lock": "0.16.1",
        "path": "pyproject.toml",
        "source": "dependency-group",
        "tool": tool,
    }


class DriftReportTests(unittest.TestCase):
    def report(self, report: object | None) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "toolchain-alignment.json"
            summary = Path(temp) / "summary.md"
            summary.touch()
            if report is not None:
                text = report if isinstance(report, str) else json.dumps(report)
                path.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-c", enforcement_source()],
                env={
                    "PATH": os.environ["PATH"],
                    "REPORT": str(path),
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            return (
                completed.returncode,
                completed.stdout + completed.stderr,
                summary.read_text(encoding="utf-8"),
            )

    def test_every_report_state_completes_without_failing(self) -> None:
        for report in (
            {"status": "aligned", "findings": [finding("exact_match")]},
            {"status": "not_applicable", "findings": []},
            {"status": "drift", "findings": [finding("exact_drift")]},
            {"status": "partial", "findings": []},
            None,
            "{not json",
        ):
            with self.subTest(report=report):
                code, output, _ = self.report(report)
                self.assertEqual(0, code, output)
                self.assertNotIn("::error", output)

    def test_drift_is_one_warning_per_drifting_declaration(self) -> None:
        code, output, summary = self.report(
            {
                "status": "drift",
                "findings": [
                    finding("exact_match", "mypy"),
                    finding("exact_drift"),
                    finding("range_admits_older", "python"),
                ],
            }
        )
        self.assertEqual(0, code)
        self.assertIn(
            "::warning file=pyproject.toml,line=7::ruff 0.0.1 is not Core's locked "
            "0.16.1 (exact_drift)",
            output,
        )
        self.assertIn("python 0.0.1", output)
        self.assertNotIn("mypy", output)
        self.assertIn("Toolchain drift (advisory): `drift`, 2 declaration(s)", summary)

    def test_an_unreadable_report_is_a_warning_not_a_pass_claim(self) -> None:
        code, output, summary = self.report(None)
        self.assertEqual(0, code)
        self.assertIn("::warning::toolchain alignment report unavailable", output)
        self.assertEqual("", summary)


class WiringTests(unittest.TestCase):
    def test_the_drift_report_runs_after_the_alignment_report(self) -> None:
        names = [s.get("id") or s.get("name") for s in steps()]
        self.assertLess(names.index("toolchain"), names.index(ENFORCE))
        self.assertLess(
            names.index("Enforce repository verification"), names.index(ENFORCE)
        )
        enforce = step(ENFORCE)
        self.assertEqual("always()", enforce["if"])
        self.assertNotIn("continue-on-error", enforce)
        self.assertEqual(
            ".l9/runtime/toolchain-alignment.json", enforce["env"]["REPORT"]
        )
        self.assertNotIn("MODE", str(enforce["run"]))
        self.assertNotIn("sys.exit", str(enforce["run"]))
        self.assertNotIn("exit 1", str(enforce["run"]))

    def test_the_report_step_is_the_pinned_core_action(self) -> None:
        [report] = [s for s in steps() if s.get("id") == "toolchain"]
        self.assertTrue(
            str(report["uses"]).startswith(
                "Quantum-L9/l9-ci-core/.github/actions/check-toolchain-alignment@"
            )
        )
        self.assertNotIn("(advisory)", str(report["name"]))


if __name__ == "__main__":
    unittest.main()
