from __future__ import annotations
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Phase3WorkflowTests(unittest.TestCase):
    def test_governance_workflow_is_read_only(self) -> None:
        path = ROOT / ".github/workflows/governance-ci.yml"
        text = path.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(r"(?m)^permissions:\s*\n\s+contents:\s+read\s*$"),
        )
        self.assertNotIn(": write", text)

    def test_profile_workflow_calls_phase_2_workflow(self) -> None:
        path = ROOT / ".github/workflows/profile-normalize-semgrep.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "uses: Quantum-L9/l9-ci-core/.github/workflows/"
            "normalize-semgrep-report.yml@ed84dcf91e539cbbdd9e2ae5303dcc42dd84ea3d",
            text,
        )
        self.assertIn(
            "uses: Quantum-L9/l9-ci-core/.github/actions/"
            "resolve-governance@def55c54ff4ba654c2ebea088dde71db0b5f7135",
            text,
        )
        self.assertIn(
            "policy: ${{ needs.governance.outputs.sdk-policy }}",
            text,
        )
        self.assertIn(
            "required-provider: "
            "${{ needs.governance.outputs.required-provider == 'true' }}",
            text,
        )

    def test_disabled_mode_skips_normalization(self) -> None:
        path = ROOT / ".github/workflows/profile-normalize-semgrep.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "if: needs.governance.outputs.enabled == 'true'",
            text,
        )
        self.assertIn(
            "if: needs.governance.outputs.enabled != 'true'",
            text,
        )


if __name__ == "__main__":
    unittest.main()
