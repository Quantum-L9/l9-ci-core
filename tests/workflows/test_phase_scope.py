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
            # v2 orchestration handoff — the Core-owned end-to-end analysis
            # kernel consumers call as a reusable workflow (workflow_call
            # only); wraps provider execution, SDK gate evaluation, artifact
            # upload, and publication.
            "analyze-semgrep.yml",
            "governance-ci.yml",
            "profile-normalize-semgrep.yml",
            "publish-analysis.yml",
            "release-validation.yml",
            "baseline-ratchet.yml",
            # Self-only dogfood callers — exercise analyze-semgrep / security
            # kernels on this repo's PRs. Not a reusable consumer surface.
            "self-analysis.yml",
            "self-security.yml",
            # v1 compatibility kernels — reusable workflows restoring the
            # @v1 contracts consumed by the Quantum-L9/.github org starters.
            "pr-pipeline.yml",
            "pre-commit-ci.yml",
            "nightly.yml",
            "release-publish.yml",
            "trio-governance.yml",
            "security.yml",
            "scorecard.yml",
            "sbom.yml",
            # The single organization-facing Core entrypoint
            # (l9.org-runtime-contract/v1) selected by l9-ci-control-plane at
            # a full immutable Core SHA. Reusable workflow_call only.
            "org-ci.yml",
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
