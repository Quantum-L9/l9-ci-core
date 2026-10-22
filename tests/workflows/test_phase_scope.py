from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Retired on 2026-10-22 (AGENTS.md §15): the v1 reusable kernels and the
# consumer-concept ratchet. SHA-pinned external callers keep resolving the
# revision they pinned; nothing on main may reference these names again.
RETIRED_WORKFLOWS = {
    "pr-pipeline.yml",
    "pre-commit-ci.yml",
    "nightly.yml",
    "release-publish.yml",
    "trio-governance.yml",
    "scorecard.yml",
    "sbom.yml",
    "baseline-ratchet.yml",
}

# Copy-first distribution trees retired with them.
RETIRED_TREES = (
    "presets/python",
    "presets/typescript/.github",
    "starter-workflows",
    "docs/templates",
    "skills",
    ".github/governance",
)


class PhaseScopeTests(unittest.TestCase):
    def test_workflow_inventory_is_exact(self) -> None:
        actual = {path.name for path in (ROOT / ".github/workflows").glob("*.yml")}
        expected = {
            "self-ci.yml",
            "sdk-contract-check.yml",
            "normalize-semgrep-report.yml",
            "analyze-semgrep.yml",
            "governance-ci.yml",
            "profile-normalize-semgrep.yml",
            "publish-analysis.yml",
            "publish-sarif.yml",
            "release-validation.yml",
            "self-analysis.yml",
            "self-security.yml",
            "security.yml",
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

    def test_retired_workflows_do_not_return(self) -> None:
        present = {path.name for path in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(set(), RETIRED_WORKFLOWS & present)

    def test_retired_copy_first_trees_do_not_return(self) -> None:
        for relative in RETIRED_TREES:
            with self.subTest(tree=relative):
                self.assertFalse((ROOT / relative).exists(), f"{relative} is retired")

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
