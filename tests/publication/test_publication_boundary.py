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
            "SARIF preflight validates only the SDK projection transport envelope;",
            "never parses findings from SARIF.",
            "Direct analysis is artifact-only and requests no checks or security-events",
            "Check and SARIF publication are independently elected false-default",
            "Blocking enforcement occurs only after immutable analysis evidence and its",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_publisher_does_not_parse_sdk_findings(self) -> None:
        text = (
            (ROOT / ".github/actions/publish-check/publish.py")
            .read_text(encoding="utf-8")
            .lower()
        )
        for value in (
            'document.get("findings")',
            'document["findings"]',
            'run.get("results")',
            'run["results"]',
            'result.get("ruleid")',
            'result["ruleid"]',
        ):
            with self.subTest(value=value):
                self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
