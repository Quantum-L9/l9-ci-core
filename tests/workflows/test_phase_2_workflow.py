from __future__ import annotations
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/normalize-semgrep-report.yml"


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


if __name__ == "__main__":
    unittest.main()
