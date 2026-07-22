from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CanonicalGateFlowTests(unittest.TestCase):
    def test_analyze_workflow_evaluates_routes_and_manifests_gate(self) -> None:
        text = (ROOT / ".github/workflows/analyze-semgrep.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("operation: gate-evaluate", text)
        self.assertIn("gate-result: .l9/runtime/${{ inputs.matrix-id }}/gate-result.json", text)
        self.assertIn("gate-result: ${{ steps.route.outputs.gate-result }}", text)
        self.assertIn("operation: semgrep-run", text)
        self.assertNotIn("semgrep scan \\", text)

    def test_publish_workflow_revalidates_and_byte_compares_gate(self) -> None:
        text = (ROOT / ".github/workflows/publish-analysis.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("operation: gate-evaluate", text)
        self.assertIn("cmp --silent", text)
        self.assertIn("gate-result: ${{ steps.locate.outputs.gate }}", text)


if __name__ == "__main__":
    unittest.main()
