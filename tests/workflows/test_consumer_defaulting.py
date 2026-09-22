"""Direct Core analysis must not require a copied consumer governance pack."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "analyze-semgrep.yml"


def workflow() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = document[True] if True in document else document["on"]
    return trigger["workflow_call"]


class ConsumerDefaultingTests(unittest.TestCase):
    def test_direct_call_has_no_required_policy_or_routing_inputs(self) -> None:
        inputs = workflow()["inputs"]
        self.assertFalse(inputs["profile"]["required"])
        self.assertEqual("", inputs["profile"]["default"])
        self.assertFalse(inputs["matrix-id"]["required"])
        self.assertEqual("pr-semgrep", inputs["matrix-id"]["default"])
        self.assertNotIn("governance-root", inputs)
        self.assertFalse(inputs["language"]["required"])
        self.assertEqual("", inputs["language"]["default"])

    def test_core_derives_normalized_event_and_profile_before_governance(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        defaults = text.index("name: Resolve Core execution defaults")
        governance = text.index("name: Resolve governance (Core)")
        self.assertLess(defaults, governance)
        for source, event, profile in (
            ("pull_request", "pull_request", "pr_fast"),
            ("push", "push", "merge"),
            ("merge_group|merge", "merge", "merge"),
            ("schedule|workflow_dispatch|nightly", "nightly", "nightly"),
            ("release", "release", "release"),
            ("supply_chain", "supply_chain", "supply_chain"),
        ):
            with self.subTest(source=source):
                self.assertIn(source, text)
                self.assertIn(f'event="{event}"', text)
                self.assertIn(f'default_profile="{profile}"', text)
        self.assertIn("profile: ${{ steps.contract.outputs.profile }}", text)
        self.assertIn("event-name: ${{ steps.contract.outputs.event }}", text)

    def test_governance_resolution_uses_bundled_core_defaults(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("governance-root", text)
        self.assertNotIn("default: .github/governance", text)
        self.assertIn("Resolve governance (Core)", text)

    def test_sdk_detects_language_instead_of_requiring_a_consumer_choice(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        detect = text.index("name: Detect repository language (SDK)")
        run = text.index("name: Run + normalize semgrep (SDK)")
        self.assertLess(detect, run)
        self.assertIn("actions/detect-language@", text)
        self.assertIn("language: ${{ steps.language.outputs.language }}", text)
        self.assertIn("steps.language.outputs.language != 'none'", text)
        self.assertIn("Validate compatibility language assertion", text)
        self.assertIn("profile: ${{ needs.analyze.outputs.profile }}", text)


if __name__ == "__main__":
    unittest.main()
