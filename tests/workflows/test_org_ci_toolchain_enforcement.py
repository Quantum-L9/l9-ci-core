"""``Analyze (central Core)`` fails on toolchain drift (L2-closeout item 2).

The alignment action writes ``.l9/runtime/toolchain-alignment.json`` and never
fails on a consumer finding itself. org-ci.yml's "Enforce toolchain alignment"
step turns the report's own ``status`` into the job verdict on every profile,
and fails closed when the report is missing or unreadable. These tests execute
that embedded step against each kind of report.
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
ENFORCE = "Enforce toolchain alignment"


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


class EnforcementTests(unittest.TestCase):
    def enforce(self, report: object | None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "toolchain-alignment.json"
            if report is not None:
                text = report if isinstance(report, str) else json.dumps(report)
                path.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-c", enforcement_source()],
                env={"PATH": os.environ["PATH"], "REPORT": str(path)},
                capture_output=True,
                text=True,
                check=False,
            )
            return completed.returncode, completed.stdout + completed.stderr

    def test_aligned_and_not_applicable_pass(self) -> None:
        for status, findings in (
            ("aligned", [finding("exact_match")]),
            ("not_applicable", []),
        ):
            with self.subTest(status=status):
                code, output = self.enforce({"status": status, "findings": findings})
                self.assertEqual(0, code, output)

    def test_drift_fails_and_annotates_only_the_drifting_declarations(self) -> None:
        report = {
            "status": "drift",
            "findings": [
                finding("exact_match", "mypy"),
                finding("exact_drift"),
                finding("range_admits_older", "python"),
            ],
        }
        code, output = self.enforce(report)
        self.assertNotEqual(0, code)
        self.assertIn(
            "::error file=pyproject.toml,line=7::ruff 0.0.1 is not Core's locked 0.16.1 "
            "(exact_drift)",
            output,
        )
        self.assertIn("python 0.0.1", output)
        self.assertNotIn("mypy", output)

    def test_a_missing_or_unreadable_report_fails_closed(self) -> None:
        for report in (None, "{not json", {"findings": []}, {"status": "aligned"}):
            with self.subTest(report=report):
                code, output = self.enforce(report)
                self.assertNotEqual(0, code)
                self.assertIn("failing closed", output)

    def test_an_unknown_status_is_never_a_pass(self) -> None:
        code, _ = self.enforce({"status": "partial", "findings": []})
        self.assertNotEqual(0, code)


class WiringTests(unittest.TestCase):
    def test_enforcement_runs_on_every_profile_after_the_report(self) -> None:
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
