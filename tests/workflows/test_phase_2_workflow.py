from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/normalize-semgrep-report.yml"
CORE_ACTION_PIN = "2a7354ed590008cd59d254df08f10d6207933366"
CORE_ACTION = re.compile(
    r"^\s*uses:\s*Quantum-L9/l9-ci-core/(\.github/actions/[A-Za-z0-9._/-]+)@"
    r"([0-9a-f]{40})\s*$",
    re.MULTILINE,
)
LOCAL_CORE_ACTION = re.compile(r"^\s*uses:\s*\./\.github/actions/", re.MULTILINE)


class Phase2WorkflowTests(unittest.TestCase):
    def test_reusable_workflow_is_present(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)

    def test_workflow_uses_read_only_permissions(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(r"(?m)^permissions:\s*\n\s+contents:\s+read\s*$"),
        )
        self.assertNotIn(": write", text)

    def test_sdk_pipeline_order_is_explicit(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        markers = [
            "Normalize provider report",
            "Validate canonical bundle",
            "Project agent-review payload",
            "Route artifacts",
            "Revalidate routed canonical bundle",
            "Build artifact manifest",
            "Upload Phase 2 artifact set",
        ]
        positions = [text.index(marker) for marker in markers]
        self.assertEqual(sorted(positions), positions)

    def test_upload_action_is_immutable(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            text,
        )

    def test_core_actions_are_fully_qualified_existing_immutable_pins(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIsNone(
            LOCAL_CORE_ACTION.search(text),
            "caller-relative Core actions resolve against the consumer checkout",
        )

        references = CORE_ACTION.findall(text)
        self.assertEqual(7, len(references))
        self.assertEqual(
            {
                ".github/actions/build-artifact-manifest",
                ".github/actions/invoke-sdk",
                ".github/actions/provision-sdk",
                ".github/actions/route-artifacts",
                ".github/actions/validate-bundle",
            },
            {path for path, _revision in references},
        )
        self.assertEqual(
            {CORE_ACTION_PIN},
            {revision for _path, revision in references},
            "normalization must use the existing Core action pin already exercised "
            "by org-ci.yml and analyze-semgrep.yml",
        )


if __name__ == "__main__":
    unittest.main()
