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
            "analyze-semgrep.yml",
            "governance-ci.yml",
            "profile-normalize-semgrep.yml",
            "publish-analysis.yml",
            "release-validation.yml",
            "baseline-ratchet.yml",
            "self-analysis.yml",
            "self-security.yml",
            "pr-pipeline.yml",
            "pre-commit-ci.yml",
            "nightly.yml",
            "release-publish.yml",
            "trio-governance.yml",
            "security.yml",
            "scorecard.yml",
            "sbom.yml",
            # Central organization required-workflow source. GitHub org
            # rulesets target repositories and require this workflow directly;
            # consumers do not copy an L9 workflow or governance pack.
            "org-ci.yml",
            # Read-only attestation of the live GitHub control plane against
            # .l9/release-plane.yaml (organization required-workflow binding,
            # Core main protection, immutable releases). Core-only governance
            # assurance: no governed repository runs it.
            "control-plane-attestation.yml",
        }
        self.assertEqual(expected, actual)

    def test_phase_4_actions_exist(self) -> None:
        required = {
            "render-publication",
            "publish-check",
            "validate-release",
            "resolve-consumer-metadata",
        }
        actual = {
            path.name for path in (ROOT / ".github/actions").iterdir() if path.is_dir()
        }
        self.assertTrue(required.issubset(actual))


if __name__ == "__main__":
    unittest.main()
