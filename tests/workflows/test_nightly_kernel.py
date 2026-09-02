"""Contract for the org nightly kernel (supersedes the v1 tests-only shim)."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NIGHTLY = ROOT / ".github/workflows/nightly.yml"
PROFILES = ROOT / ".github/governance/execution-profiles.yaml"
ANALYZE_PIN = re.compile(
    r"uses:\s*Quantum-L9/l9-ci-core/\.github/workflows/"
    r"analyze-semgrep\.yml@[0-9a-f]{40}"
)
CORE_ACTIONS_PIN = "01f5b16b3520ce75c168c5720864dfeddd5423a9"
JOB_ID = re.compile(r"(?m)^  ([A-Za-z][A-Za-z0-9_-]*):")


class NightlyKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = NIGHTLY.read_text(encoding="utf-8")

    def test_nests_analyze_semgrep_at_core_actions_pin(self) -> None:
        self.assertRegex(self.text, ANALYZE_PIN)
        self.assertIn("CORE_ACTIONS_PIN", self.text)
        self.assertIn(
            f"analyze-semgrep.yml@{CORE_ACTIONS_PIN}",
            self.text,
        )
        self.assertNotRegex(
            self.text,
            re.compile(r"(?m)^\s*uses:\s*\./\.github/workflows/analyze-semgrep\.yml"),
        )

    def test_profile_nightly_and_matrix_id(self) -> None:
        self.assertRegex(self.text, re.compile(r"(?m)^\s+profile:\s+nightly\s*$"))
        self.assertRegex(
            self.text,
            re.compile(r"(?m)^\s+matrix-id:\s+nightly-semgrep\s*$"),
        )

    def test_analyze_job_grants_publication_permissions(self) -> None:
        analyze = self.text.split("nightly:", 1)[0]
        self.assertRegex(analyze, re.compile(r"(?m)^\s+actions:\s+read\s*$"))
        self.assertRegex(analyze, re.compile(r"(?m)^\s+checks:\s+write\s*$"))
        self.assertRegex(analyze, re.compile(r"(?m)^\s+contents:\s+read\s*$"))

    def test_workflow_level_contents_stay_read(self) -> None:
        header = self.text.split("jobs:", 1)[0]
        self.assertRegex(header, re.compile(r"(?m)^\s*contents:\s+read\s*$"))
        self.assertNotRegex(header, re.compile(r"(?m)^\s*contents:\s+write\s*$"))

    def test_v1_caller_inputs_remain_optional(self) -> None:
        for name in ("python-version", "run-extended-tests", "language"):
            self.assertIn(f"{name}:", self.text)
        self.assertRegex(
            self.text,
            re.compile(r"(?m)^\s+language:\s+\$\{\{\s*inputs\.language\s*\}\}\s*$"),
        )

    def test_jobs_are_analyze_tests_and_summary_only(self) -> None:
        jobs_block = self.text.split("jobs:", 1)[1]
        self.assertEqual(
            ["analyze", "nightly", "summary"],
            JOB_ID.findall(jobs_block),
        )

    def test_nightly_profile_allows_schedule_and_dispatch(self) -> None:
        payload = json.loads(PROFILES.read_text(encoding="utf-8"))
        nightly = payload["profiles"]["nightly"]
        self.assertEqual("ci_deep", nightly["sdk_profile"])
        self.assertEqual("advisory", nightly["default_mode"])
        self.assertEqual(
            ["schedule", "workflow_dispatch"],
            nightly["allowed_events"],
        )


if __name__ == "__main__":
    unittest.main()
