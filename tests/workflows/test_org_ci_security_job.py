"""org-ci runs Core's security scan and the required job enforces its result.

L2-closeout item 1 (step two). The `security` job calls `security.yml` (gitleaks
with the consumer's `.gitleaks.toml` layered on the Core preset, pip-audit,
advisory bandit) on what the event changed. The required job (`analyze`) is the one
required context, so it waits for `security`, runs whatever that job concluded,
and fails in its last step unless the scan succeeded: a skipped or cancelled
scan is INCOMPLETE and never passes.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
PIN = re.compile(
    r"^Quantum-L9/l9-ci-core/\.github/workflows/security\.yml@[0-9a-f]{40}$"
)
ENFORCE = "Enforce security scan (secrets, dependency audit)"


def workflow() -> dict:
    return yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))


def squash(text: object) -> str:
    return " ".join(str(text).split())


class SecurityJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = workflow()["jobs"]
        self.security = self.jobs["security"]
        self.analyze = self.jobs["analyze"]

    def test_security_calls_the_pinned_core_workflow_on_the_event_scope(self) -> None:
        self.assertRegex(self.security["uses"], PIN)
        self.assertEqual({"gitleaks-scope": "event"}, self.security["with"])
        # No secrets reach the scan, and no broader token than the workflow's.
        self.assertNotIn("secrets", self.security)
        self.assertNotIn("permissions", self.security)

    def test_security_runs_exactly_when_analyze_would(self) -> None:
        gate = squash(self.analyze["if"])
        self.assertTrue(gate.startswith("!cancelled() && ("), gate)
        self.assertEqual(
            squash(self.security["if"]), gate[len("!cancelled() && (") : -1]
        )

    def test_the_required_job_waits_for_security(self) -> None:
        self.assertEqual("security", self.analyze["needs"])

    def test_the_last_required_step_enforces_the_scan(self) -> None:
        step = self.analyze["steps"][-1]
        self.assertEqual(ENFORCE, step["name"])
        self.assertEqual("always()", step["if"])
        self.assertEqual("${{ needs.security.result }}", step["env"]["SECURITY_RESULT"])

    def test_only_success_passes(self) -> None:
        script = self.analyze["steps"][-1]["run"]
        for result, code in (
            ("success", 0),
            ("failure", 1),
            ("skipped", 1),
            ("cancelled", 1),
            ("", 1),
        ):
            with self.subTest(result=result):
                completed = subprocess.run(
                    ["bash", "-c", script],
                    env={"PATH": os.environ["PATH"], "SECURITY_RESULT": result},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(code, completed.returncode, completed.stdout)
                if code:
                    self.assertIn("::error title=Security scan::", completed.stdout)


if __name__ == "__main__":
    unittest.main()
