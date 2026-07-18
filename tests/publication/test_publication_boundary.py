from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PublicationBoundaryTests(unittest.TestCase):
    def test_renderer_does_not_reconstruct_findings(self) -> None:
        text = (
            (ROOT / ".github/actions/render-publication/render.py")
            .read_text(encoding="utf-8")
            .lower()
        )
        for value in (
            "finding-bundle.json",
            '"findings"',
            '"evidence"',
            '"classifications"',
            "rule_identity",
            "severity_normal",
        ):
            with self.subTest(value=value):
                self.assertNotIn(value, text)

    def test_contract_preserves_sdk_gate_ownership(self) -> None:
        text = (ROOT / ".l9/publication-contract.yaml").read_text(encoding="utf-8")
        for value in (
            "Core must never derive a gate from finding counts or severities.",
            "Core must never change the SDK gate conclusion.",
            "Core may consume the SDK-owned agent-review projection.",
            "Pull-request source code is never executed in the publication job.",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
