from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class GovernanceBoundaryTests(unittest.TestCase):
    def test_resolver_does_not_read_canonical_artifacts(self) -> None:
        text = (
            (ROOT / ".github/actions/resolve-governance/resolve.py")
            .read_text(encoding="utf-8")
            .lower()
        )
        prohibited = (
            "finding-bundle.json",
            "agent-review-payload.json",
            '"findings"',
            '"classifications"',
            '"evidence"',
            "severity",
        )
        for value in prohibited:
            with self.subTest(value=value):
                self.assertNotIn(value, text)

    def test_governance_contract_prohibits_second_gate(self) -> None:
        text = (ROOT / ".l9/governance-contract.yaml").read_text(encoding="utf-8")
        required = (
            "Core reading canonical findings to calculate a gate",
            "Core deriving rule identity",
            "Core mutating a canonical bundle",
            "invalid canonical bundle",
            "artifact compatibility failure",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
