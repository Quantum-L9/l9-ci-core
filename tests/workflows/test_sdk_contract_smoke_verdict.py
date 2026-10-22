"""The SDK contract smoke proves the central policy blocks (AGENTS.md §16).

``sdk-contract-check.yml`` drives one committed fixture finding per language
through ``semgrep normalize`` under the central policy and identity map and
then ``gate evaluate``. Since the central policy defaults every finding to
``blocking``, the only correct verdict is ``fail`` with exactly one blocking
finding — and the smoke asserts that verdict instead of letting ``set -e``
abort on the gate's exit 1. A policy that quietly stopped blocking would
flip this verdict to ``pass`` and fail the smoke.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "sdk-contract-check.yml"
POLICY = ROOT / ".github" / "actions" / "resolve-governance" / "semgrep-policy.yaml"
FIXTURES = ROOT / "tests" / "fixtures" / "smoke"


class SmokeVerdictTests(unittest.TestCase):
    def test_smoke_uses_the_central_policy_and_identity_maps(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'policy="${WORKSPACE}/.github/actions/resolve-governance/semgrep-policy.yaml"',
            text,
        )
        self.assertIn(
            'identity="${WORKSPACE}/.github/actions/resolve-governance/identity-maps/${lang}.yaml"',
            text,
        )
        self.assertNotIn("presets/", text)

    def test_smoke_asserts_a_blocking_fail_verdict(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("|| gate_rc=$?", text)
        self.assertIn('result.get("status") != "fail" or rc != 1', text)
        self.assertIn('len(result.get("blocking_finding_ids", [])) != 1', text)

    def test_central_policy_blocks_by_default_and_fixtures_hit_the_default(
        self,
    ) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual("blocking", policy["defaults"]["mode"])
        for rule in policy["rules"].values():
            self.assertIn(rule["mode"], {"advisory", "blocking", "shadow", "disabled"})
            self.assertTrue(rule["policy_key"])
        for language in ("python", "typescript"):
            report = json.loads(
                (FIXTURES / f"{language}-report.json").read_text("utf-8")
            )
            ids = [item["check_id"] for item in report["results"]]
            self.assertEqual(1, len(ids), language)
            self.assertNotIn(
                ids[0], policy["rules"], f"{language} fixture must hit the default"
            )


if __name__ == "__main__":
    unittest.main()
