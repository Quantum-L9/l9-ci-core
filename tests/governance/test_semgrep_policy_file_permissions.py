"""The central policy classifies insecure-file-permissions as advisory (version 4).

``insecure-file-permissions`` (Semgrep community, ``audit``) flags ``os.chmod``
/ ``lchmod`` / ``fchmod`` when the literal mode satisfies ``$BITS >= 0o650``.
That compares magnitudes, not permission bits: owner-only ``0o700`` (no group
or other bits) is reported as widely permissive, while ``0o555`` passes. It
blocked required CI on an owner-only staging directory
(Quantum-L9/Enrichment.Inference.Engine#287). It is advisory: still reported,
reviewed per finding.

These tests fail if the rule is promoted back to blocking without review, if
the policy version is not advanced with the classification, or if the blocking
default itself moves.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / ".github" / "actions" / "resolve-governance" / "semgrep-policy.yaml"
MODE_MAGNITUDE_RULE = (
    "python.lang.security.audit.insecure-file-permissions.insecure-file-permissions"
)


class FilePermissionsPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = json.loads(POLICY.read_text(encoding="utf-8"))

    def test_mode_magnitude_rule_is_advisory_and_keyed(self) -> None:
        rule = self.policy["rules"].get(MODE_MAGNITUDE_RULE)
        self.assertIsNotNone(rule, "insecure-file-permissions must be classified")
        self.assertEqual("advisory", rule["mode"])
        self.assertEqual("filesystem.mode-magnitude-comparison", rule["policy_key"])

    def test_classification_advances_the_policy_version(self) -> None:
        self.assertEqual("4", self.policy["metadata"]["version"])

    def test_blocking_default_is_unchanged(self) -> None:
        self.assertEqual("blocking", self.policy["defaults"]["mode"])

    def test_note_records_why(self) -> None:
        note = self.policy["note"]
        self.assertIn("version 4", note)
        self.assertIn("insecure-file-permissions", note)
        self.assertIn("0o650", note)
        self.assertIn("Enrichment.Inference.Engine#287", note)


if __name__ == "__main__":
    unittest.main()
