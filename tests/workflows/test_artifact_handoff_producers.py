from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
PRODUCERS = (
    "org-ci.yml",
    "analyze-semgrep.yml",
    "normalize-semgrep-report.yml",
)
HANDOFF_ACTION = re.compile(
    r"uses:\s*Quantum-L9/l9-ci-core/\.github/actions/"
    r"create-artifact-handoff@[0-9a-f]{40}"
)


class ArtifactHandoffProducerWorkflowTests(unittest.TestCase):
    def test_actual_phase_2_producers_emit_after_successful_upload(self) -> None:
        for name in PRODUCERS:
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text(encoding="utf-8")
                upload = text.index("uses: actions/upload-artifact@")
                handoff = text.index("name: Create artifact handoff descriptor")
                self.assertLess(upload, handoff)
                self.assertRegex(text[handoff:], HANDOFF_ACTION)
                for value in (
                    "artifact-id: ${{ steps.upload.outputs.artifact-id }}",
                    "artifact-digest: ${{ steps.upload.outputs.artifact-digest }}",
                    "repository: ${{ github.repository }}",
                    "repository-revision: ${{ github.sha }}",
                ):
                    self.assertIn(value, text[handoff:])
                self.assertIn("artifact-handoff:", text[:upload])
                self.assertNotIn("handoff-descriptor:", text[:upload])

    def test_publication_workflows_consume_optional_descriptor(self) -> None:
        for name in ("publish-analysis.yml", "publish-sarif.yml"):
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIn("artifact-handoff:", text)
                self.assertIn(
                    "handoff-descriptor: ${{ inputs.artifact-handoff }}", text
                )

    def test_direct_analysis_marks_evidence_ready_after_handoff_before_enforcement(
        self,
    ) -> None:
        text = (WORKFLOWS / "analyze-semgrep.yml").read_text(encoding="utf-8")
        upload = text.index("name: Upload analysis artifact set")
        handoff = text.index("name: Create artifact handoff descriptor")
        ready = text.index("name: Mark uploaded evidence ready")
        enforce = text.index("name: Enforce SDK gate verdict")
        self.assertEqual(
            sorted((upload, handoff, ready, enforce)), [upload, handoff, ready, enforce]
        )
        self.assertIn("evidence-ready:", text[:upload])
        self.assertIn("evidence-ready=true", text[ready:enforce])

    def test_no_other_workflow_produces_a_handoff_descriptor(self) -> None:
        expected = set(PRODUCERS)
        actual = {
            path.name
            for path in WORKFLOWS.glob("*.yml")
            if "create-artifact-handoff@" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
