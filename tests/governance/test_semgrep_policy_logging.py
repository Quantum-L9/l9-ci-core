"""The central policy's logging classifications stay value-based (version 3).

``python-logger-credential-disclosure`` (Semgrep community, ``audit``) decides
from the logger FORMAT STRING alone: a secret word anywhere before a ``%s``.
It never looks at the logged arguments, so it blocked required CI on
``"Invalid API key: %s %s from %s"`` logging a request's method, path and host
(Quantum-L9/Cognitive.Engine.Graphs PR #409). It is advisory: still reported,
reviewed per finding.

That is only safe while the L9-owned rules that inspect the argument
expressions keep blocking. These tests fail if the community heuristic is
promoted back to blocking without review, or if any L9 logging rule is
classified below the blocking default.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / ".github" / "actions" / "resolve-governance" / "semgrep-policy.yaml"
COMMUNITY_HEURISTIC = (
    "python.lang.security.audit.logging.logger-credential-leak"
    ".python-logger-credential-disclosure"
)
L9_LOGGING_RULES = (
    "l9.logging.forbidden-secret-field",
    "l9.logging.forbidden-secret-keyword",
    "l9.logging.f-string-secret",
)


class LoggingPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = json.loads(POLICY.read_text(encoding="utf-8"))

    def test_format_string_heuristic_is_advisory_and_keyed(self) -> None:
        rule = self.policy["rules"].get(COMMUNITY_HEURISTIC)
        self.assertIsNotNone(rule, "community heuristic must be classified")
        self.assertEqual("advisory", rule["mode"])
        self.assertEqual("logging.credential-word-in-format-string", rule["policy_key"])

    def test_l9_value_rules_keep_the_blocking_default(self) -> None:
        self.assertEqual("blocking", self.policy["defaults"]["mode"])
        for provider_rule_id, rule in self.policy["rules"].items():
            for l9_rule in L9_LOGGING_RULES:
                if provider_rule_id.endswith(l9_rule):
                    self.assertEqual(
                        "blocking",
                        rule["mode"],
                        f"{provider_rule_id} carries the blocking guarantee the "
                        "advisory format-string heuristic relies on",
                    )

    def test_note_records_why(self) -> None:
        note = self.policy["note"]
        self.assertIn("python-logger-credential-disclosure", note)
        self.assertIn("AST-LOGGING-001/002/003", note)


if __name__ == "__main__":
    unittest.main()
