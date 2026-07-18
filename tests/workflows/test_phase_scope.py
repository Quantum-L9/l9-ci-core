from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PhaseScopeTests(unittest.TestCase):
    def test_phase_1_through_phase_4_workflows_exist(self) -> None:
        actual = {path.name for path in (ROOT / ".github/workflows").glob("*.yml")}
        expected = {
            "self-ci.yml",
            "sdk-contract-check.yml",
            "normalize-semgrep-report.yml",
            "governance-ci.yml",
            "profile-normalize-semgrep.yml",
            "publish-analysis.yml",
            "release-validation.yml",
        }
        self.assertEqual(expected, actual)

    def test_phase_4_actions_exist(self) -> None:
        required = {"render-publication", "publish-check", "validate-release"}
        actual = {
            path.name for path in (ROOT / ".github/actions").iterdir() if path.is_dir()
        }
        self.assertTrue(required.issubset(actual))


if __name__ == "__main__":
    unittest.main()
