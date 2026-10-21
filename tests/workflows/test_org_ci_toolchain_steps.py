"""org-ci.yml activates Core's toolchain SSOT actions (CG-TOOLCHAIN-SSOT PR-B).

PR-A landed ``check-toolchain-alignment``, ``install-biome`` and
``run-biome-check``; this activation pins them in the required ``analyze`` job.
These tests keep the activation honest: every edge is a full-SHA Core action
pin shared by the three steps, the job stays ``contents: read``, the steps are
advisory (they run before, and never as part of, any enforcement step), Biome
is not downloaded for a consumer without a Biome config, and no SDK Biome
workflow is referenced.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
ACTIONS = ("check-toolchain-alignment", "install-biome", "run-biome-check")
PIN = re.compile(
    r"^Quantum-L9/l9-ci-core/\.github/actions/(?P<action>[a-z-]+)@(?P<sha>[0-9a-f]{40})$"
)


def analyze_steps() -> list[dict[str, Any]]:
    document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = document["jobs"]["analyze"]["steps"]
    return steps


def step_index(steps: list[dict[str, Any]], action: str) -> int:
    for index, step in enumerate(steps):
        match = PIN.match(str(step.get("uses", "")))
        if match and match.group("action") == action:
            return index
    raise AssertionError(f"org-ci analyze job does not use {action}")


class ToolchainActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.steps = analyze_steps()

    def test_each_action_is_pinned_to_one_shared_full_sha(self) -> None:
        shas = set()
        for action in ACTIONS:
            with self.subTest(action=action):
                step = self.steps[step_index(self.steps, action)]
                match = PIN.match(step["uses"])
                assert match is not None
                shas.add(match.group("sha"))
        self.assertEqual(1, len(shas), f"toolchain actions must share one pin: {shas}")

    def test_pins_are_marked_as_core_action_pins(self) -> None:
        text = ORG_CI.read_text(encoding="utf-8").splitlines()
        for action in ACTIONS:
            with self.subTest(action=action):
                line = next(
                    number
                    for number, content in enumerate(text)
                    if f"/.github/actions/{action}@" in content
                )
                self.assertIn("# CORE_ACTIONS_PIN", text[line - 1])

    def test_job_stays_read_only(self) -> None:
        document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        self.assertEqual({"contents": "read"}, document["permissions"])
        self.assertEqual(
            {"contents": "read"}, document["jobs"]["analyze"]["permissions"]
        )

    def test_alignment_runs_right_after_the_consumer_checkout(self) -> None:
        checkout = next(
            index
            for index, step in enumerate(self.steps)
            if step.get("name") == "Checkout immutable event revision"
        )
        self.assertEqual(
            checkout + 1, step_index(self.steps, "check-toolchain-alignment")
        )

    def test_biome_follows_language_detection_and_install_precedes_run(self) -> None:
        language = next(
            index
            for index, step in enumerate(self.steps)
            if step.get("id") == "language"
        )
        install = step_index(self.steps, "install-biome")
        run = step_index(self.steps, "run-biome-check")
        self.assertLess(language, install)
        self.assertEqual(install + 1, run)

    def test_biome_is_advisory_and_independent_of_semgrep_governance(self) -> None:
        install = self.steps[step_index(self.steps, "install-biome")]
        run = self.steps[step_index(self.steps, "run-biome-check")]
        self.assertEqual("advisory", run["with"]["mode"])
        self.assertEqual(
            "${{ steps.biome.outputs.executable }}", run["with"]["executable"]
        )
        self.assertEqual("hashFiles('biome.json', 'biome.jsonc') != ''", install["if"])
        self.assertNotIn("biome-version", install.get("with", {}))
        for step in (install, run):
            self.assertNotIn("steps.gov.outputs", str(step.get("if", "")))
            self.assertNotIn("continue-on-error", step)

    def test_toolchain_steps_precede_every_enforcement_step(self) -> None:
        last = max(step_index(self.steps, action) for action in ACTIONS)
        enforcement = [
            index
            for index, step in enumerate(self.steps)
            if str(step.get("name", "")).startswith("Enforce")
        ]
        self.assertTrue(enforcement)
        self.assertLess(last, min(enforcement))

    def test_summary_reports_both_advisory_results(self) -> None:
        summary = next(
            step
            for step in self.steps
            if step.get("name") == "Write central CI summary"
        )
        self.assertIn("TOOLCHAIN_ALIGNMENT", summary["env"])
        self.assertIn("BIOME_STATUS", summary["env"])

    def test_no_sdk_biome_workflow_is_referenced(self) -> None:
        self.assertNotIn("l9-biome-scan", ORG_CI.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
