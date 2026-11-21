"""Retargeting a pull request's base re-evaluates the required check.

Quantum-L9/Cursor-Governance#956: when a stacked parent merges and its child
pull request is retargeted onto `main`, GitHub emits only `pull_request`
activity type `edited` (with `github.event.changes.base` present). The default
`pull_request` activity types are `opened`, `synchronize` and `reopened`, so
the required check was never evaluated for the new
base.

A title or body edit is deliberately *not* skipped: a job skipped by `if:`
reports Success and does not block merging even when it is a required check,
so a skip would let a title edit post a passing check over a failing one on
the same head SHA. Every `edited` event therefore re-evaluates the same
immutable head SHA.
"""

from __future__ import annotations

import unittest

from tests.workflows.test_org_ci_event_resolution import (
    evaluate_gate,
    load_workflow,
    resolve,
)

DEFAULT_TYPES = {"opened", "synchronize", "reopened"}


class BaseRetargetTriggerTests(unittest.TestCase):
    def pull_request_types(self) -> list[str]:
        trigger = load_workflow()["on"]["pull_request"]
        self.assertIsInstance(trigger, dict, "pull_request must declare types")
        types = trigger["types"]
        self.assertIsInstance(types, list)
        return types

    def gate(self) -> str:
        return " ".join(load_workflow()["jobs"]["analyze"]["if"].split())

    def test_edited_is_declared(self) -> None:
        self.assertIn("edited", self.pull_request_types())

    def test_default_activity_types_are_kept(self) -> None:
        """Declaring `types` replaces GitHub's defaults; none may be lost."""
        self.assertEqual(DEFAULT_TYPES | {"edited"}, set(self.pull_request_types()))
        self.assertEqual(
            len(self.pull_request_types()), len(set(self.pull_request_types()))
        )

    def test_pull_request_carries_no_branch_or_path_filter(self) -> None:
        """Repository and ref targeting stays the organization ruleset's."""
        trigger = load_workflow()["on"]["pull_request"]
        self.assertEqual({"types"}, set(trigger))

    def test_a_base_change_runs_the_analysis(self) -> None:
        self.assertTrue(
            evaluate_gate(
                self.gate(),
                event_name="pull_request",
                ref_name="123/merge",
                inputs_event="",
                default_branch="main",
            )
        )

    def test_a_title_or_body_edit_is_never_skipped(self) -> None:
        """A skip would post Success over a failing required check.

        The gate may not depend on the edit's content: it must not read
        `github.event.action` or `github.event.changes`, so a title edit and
        a base change evaluate identically.
        """
        gate = self.gate()
        self.assertNotIn("github.event.action", gate)
        self.assertNotIn("github.event.changes", gate)
        for step in load_workflow()["jobs"]["analyze"]["steps"]:
            condition = str(step.get("if", ""))
            self.assertNotIn("github.event.changes", condition)
            self.assertNotIn("github.event.action", condition)

    def test_an_edited_event_resolves_to_pr_fast(self) -> None:
        """`edited` is still `pull_request`: same class, same profile."""
        outputs = resolve(event_name="pull_request")
        self.assertIsInstance(outputs, dict)
        assert isinstance(outputs, dict)
        self.assertEqual("pull_request", outputs["event-class"])
        self.assertEqual("pr_fast", outputs["profile"])

    def test_concurrency_and_permissions_are_unchanged(self) -> None:
        document = load_workflow()
        self.assertEqual(
            "l9-core-${{ github.repository }}-${{ github.sha }}-"
            "${{ github.event_name }}",
            " ".join(document["concurrency"]["group"].split()),
        )
        self.assertIs(False, document["concurrency"]["cancel-in-progress"])
        self.assertEqual({"contents": "read"}, document["permissions"])
        self.assertEqual(
            {"contents": "read"}, document["jobs"]["analyze"]["permissions"]
        )


if __name__ == "__main__":
    unittest.main()
