from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CanonicalGateDocumentationTests(unittest.TestCase):
    def test_artifact_protocol_names_gate_as_verdict_authority(self) -> None:
        text = (ROOT / "docs/artifact-protocol.md").read_text(encoding="utf-8")
        self.assertIn("canonical gate-result.json", text)
        self.assertIn("verdict authority", text)

    def test_consumer_template_is_thin_core_caller(self) -> None:
        text = (ROOT / "docs/templates/l9-analysis.yml").read_text(encoding="utf-8")
        self.assertIn("analyze-semgrep.yml@", text)
        self.assertNotIn("semgrep scan", text)
        self.assertNotIn("steps:", text)


if __name__ == "__main__":
    unittest.main()
