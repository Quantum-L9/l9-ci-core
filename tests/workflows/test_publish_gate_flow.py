from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PublishGateFlowTests(unittest.TestCase):
    def test_publication_revalidates_and_byte_compares_gate(self) -> None:
        text = (ROOT / ".github/workflows/publish-analysis.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("gate-result.json", text)
        self.assertIn("operation: gate-evaluate", text)
        self.assertIn("cmp --silent", text)
        self.assertIn("gate-result: ${{ steps.locate.outputs.gate }}", text)
        self.assertIn("sdk-revision:", text)


if __name__ == "__main__":
    unittest.main()
