"""org-ci.yml activates Core's toolchain SSOT actions (CG-TOOLCHAIN-SSOT PR-B).

PR-A landed ``check-toolchain-alignment``, ``install-biome`` and
``run-biome-check``. Toolchain alignment runs in the required ``analyze`` job;
Biome is its own check, the ``biome`` job (operator, 2026-11-21;
docs/biome-check.md), which installs Biome with ``install-biome`` and runs it
itself on the changed files. These tests keep the activation honest: every
edge is a full-SHA Core action pin shared across org-ci.yml, the jobs stay
``contents: read``, alignment is advisory (it runs before, and never as part
of, any enforcement step), Biome is not downloaded for a consumer without a
Biome config, and no SDK Biome workflow is referenced.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
# The job each toolchain action runs in. run-biome-check is not used by
# org-ci.yml any more: the Biome job runs `biome ci` on the changed files itself.
ACTIONS = {"check-toolchain-alignment": "analyze", "install-biome": "biome"}
PIN = re.compile(
    r"^Quantum-L9/l9-ci-core/\.github/actions/(?P<action>[a-z-]+)@(?P<sha>[0-9a-f]{40})$"
)


def job_steps(job: str = "analyze") -> list[dict[str, Any]]:
    document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = document["jobs"][job]["steps"]
    return steps


def analyze_steps() -> list[dict[str, Any]]:
    return job_steps("analyze")


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
        for action, job in ACTIONS.items():
            with self.subTest(action=action):
                steps = job_steps(job)
                step = steps[step_index(steps, action)]
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

    def test_biome_is_its_own_blocking_check_independent_of_governance(self) -> None:
        """Biome blocks on findings fleet-wide, as the `Biome` check."""
        steps = job_steps("biome")
        install = steps[step_index(steps, "install-biome")]
        run = next(s for s in steps if s.get("id") == "biome_check")
        self.assertEqual(steps.index(install) + 1, steps.index(run))
        self.assertIn("${{ steps.biome.outputs.executable }}", str(run["env"]))
        self.assertNotIn("biome-version", install.get("with", {}))
        self.assertIn('header = "- Biome (blocking)"', run["run"])
        self.assertNotIn("Biome (advisory)", ORG_CI.read_text(encoding="utf-8"))
        for step in steps:
            self.assertNotIn("steps.gov.outputs", str(step.get("if", "")))
            self.assertNotIn("continue-on-error", step)
        for action in ("install-biome", "run-biome-check"):
            with self.assertRaises(AssertionError):
                step_index(self.steps, action)

    def test_toolchain_steps_precede_every_enforcement_step(self) -> None:
        last = step_index(self.steps, "check-toolchain-alignment")
        enforcement = [
            index
            for index, step in enumerate(self.steps)
            if str(step.get("name", "")).startswith("Enforce")
        ]
        self.assertTrue(enforcement)
        self.assertLess(last, min(enforcement))

    def test_summary_reports_alignment_and_points_at_the_biome_check(self) -> None:
        summary = next(
            step
            for step in self.steps
            if step.get("name") == "Write central CI summary"
        )
        self.assertIn("TOOLCHAIN_ALIGNMENT", summary["env"])
        self.assertNotIn("BIOME_STATUS", summary["env"])
        self.assertIn("its own check, \\`Biome\\`", summary["run"])

    def test_no_sdk_biome_workflow_is_referenced(self) -> None:
        self.assertNotIn("l9-biome-scan", ORG_CI.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
