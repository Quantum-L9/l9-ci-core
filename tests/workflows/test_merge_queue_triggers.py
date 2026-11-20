"""Every required check on Core ``main`` also reports on the merge queue.

GitHub runs a workflow for a merge queue only when its ``on:`` lists
``merge_group``; a required check that never reports there blocks the queue
until it times out (docs: "Managing a merge queue"; ruleset required workflows
likewise run only for ``pull_request``, ``pull_request_target`` and
``merge_group``). Core main uses a merge queue (docs/release/rulesets/README.md,
2026-11-20 revision), so each
workflow that produces a context in ``core-main-protection.json`` must
trigger on ``merge_group``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RULESET = ROOT / "docs" / "release" / "rulesets" / "core-main-protection.json"

# The workflow that produces each required context (docs/release/rulesets/README.md).
PRODUCERS = {
    "Lint and Type Check": "self-ci.yml",
    "Unit tests": "self-ci.yml",
    "Repository contract": "self-ci.yml",
    "Governance validate": "governance-ci.yml",
    "sdk-contract": "sdk-contract-check.yml",
    "Analyze (semgrep) / Analyze (semgrep -> SDK)": "self-analysis.yml",
    "Security scan / security": "self-security.yml",
}


def load(name: str) -> dict[str, object]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def triggers(name: str) -> dict[str, object]:
    workflow = load(name)
    found = workflow.get(True, workflow.get("on"))
    assert isinstance(found, dict), name
    return found


class MergeQueueTriggerTests(unittest.TestCase):
    def test_every_required_context_has_a_known_producer(self) -> None:
        payload = json.loads(RULESET.read_text(encoding="utf-8"))
        [rule] = [r for r in payload["rules"] if r["type"] == "required_status_checks"]
        contexts = {c["context"] for c in rule["parameters"]["required_status_checks"]}
        self.assertEqual(set(PRODUCERS), contexts)

    def test_every_producer_runs_on_merge_group(self) -> None:
        for name in sorted(set(PRODUCERS.values())):
            with self.subTest(workflow=name):
                self.assertIn("merge_group", triggers(name))
                self.assertIn("pull_request", triggers(name))

    def test_the_organization_required_workflow_runs_on_merge_group(self) -> None:
        # org-ci.yml is required on Core main by organization ruleset 21895545.
        self.assertIn("merge_group", triggers("org-ci.yml"))

    def test_the_payload_declares_the_merge_queue(self) -> None:
        payload = json.loads(RULESET.read_text(encoding="utf-8"))
        [queue] = [r for r in payload["rules"] if r["type"] == "merge_queue"]
        parameters = queue["parameters"]
        self.assertEqual("SQUASH", parameters["merge_method"])
        self.assertEqual("ALLGREEN", parameters["grouping_strategy"])
        self.assertGreaterEqual(parameters["min_entries_to_merge"], 1)
        [pull_request] = [r for r in payload["rules"] if r["type"] == "pull_request"]
        self.assertEqual(
            0, pull_request["parameters"]["required_approving_review_count"]
        )
        self.assertIs(False, pull_request["parameters"]["require_code_owner_review"])

    def test_self_analysis_maps_the_merge_group_to_the_merge_profile(self) -> None:
        analyze = load("self-analysis.yml")["jobs"]["analyze"]
        profile = " ".join(str(analyze["with"]["profile"]).split())
        self.assertIn("github.event_name == 'merge_group' && 'merge'", profile)

    def test_advisory_sonar_skips_the_merge_group(self) -> None:
        sonar = load("self-ci.yml")["jobs"]["sonar"]
        self.assertIn("github.event_name != 'merge_group'", str(sonar["if"]))


if __name__ == "__main__":
    unittest.main()
