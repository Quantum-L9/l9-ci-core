"""The Dependabot MANIFEST reseal workflow must have exactly one writer.

These tests assert the *topology* of the workflow, not the presence of
strings. The distinction matters: the earlier version of this file checked
that `pull_request:` appeared in the text, which is satisfied equally by a
safe workflow and by the two-event-family race this file now forbids.

The hazard being tested is concrete. Dependabot updates a pull request by
pushing its `dependabot/**` branch. A workflow subscribed to both `push` and
`pull_request` observes that one update twice, and the two runs carry
different `github.ref` values -- `refs/heads/dependabot/...` for the push and
`refs/pull/N/merge` for the pull request. A concurrency group keyed on the ref
therefore places them in *different* groups, so GitHub serializes neither, and
two jobs reseal and push the same branch concurrently.

The invariant that removes the hazard: one event family, and one branch
expression shared by the concurrency key, the checkout ref, and the push
target.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "manifest-reseal.yml"

# The branch this job writes to. Under a push trigger `github.ref_name` is the
# short branch name; it is the only branch expression the workflow may use.
BRANCH_EXPRESSION = "github.ref_name"


class ManifestResealWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        # `on` is parsed by PyYAML 1.1 rules as the boolean True, not the
        # string "on". Accept whichever key the loader produced so the test
        # does not silently pass against a workflow with no triggers at all.
        # Comment prose explains why a construct is forbidden and would
        # otherwise satisfy a substring search for that construct's own name.
        # Assertions about what the workflow *does* read this instead.
        self.directives = "\n".join(
            line for line in self.text.splitlines() if not line.lstrip().startswith("#")
        )
        self.workflow = yaml.safe_load(self.text)
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertIsInstance(
            triggers, dict, "workflow must declare a mapping of triggers"
        )
        self.triggers = triggers
        self.job = self.workflow["jobs"]["reseal"]

    # --- trigger topology ------------------------------------------------

    def test_push_is_the_only_event_family(self) -> None:
        """Two event families cannot be made single-flight with each other.

        This is the root-cause assertion: the race is removed by having one
        trigger, not by trying to reconcile two concurrency keys.
        """
        self.assertEqual(
            ["push"],
            sorted(self.triggers),
            "exactly one event family may drive a job that pushes to a "
            "branch; a second family observes the same Dependabot update "
            "under a different github.ref and races the first",
        )

    def test_push_is_restricted_to_dependabot_branches(self) -> None:
        self.assertEqual(
            {"branches": ["dependabot/**"]},
            self.triggers["push"],
            "the reseal job must not be reachable from non-Dependabot pushes",
        )

    def test_no_pull_request_trigger_of_any_kind(self) -> None:
        for forbidden in ("pull_request", "pull_request_target"):
            with self.subTest(trigger=forbidden):
                self.assertNotIn(forbidden, self.triggers)

    def test_pull_request_target_is_absent_from_the_source(self) -> None:
        """No privileged execution of pull-request-controlled code.

        `pull_request_target` runs with a write token against the base repo
        while checking out contributor-controlled refs. It is the obvious
        shortcut for giving this job write access and is forbidden outright.
        """
        self.assertNotIn("pull_request_target", self.directives)
        self.assertNotIn("secrets: inherit", self.directives)

    # --- single-flight ---------------------------------------------------

    def test_concurrency_key_is_the_branch_being_written(self) -> None:
        group = self.workflow["concurrency"]["group"]
        self.assertIn(
            BRANCH_EXPRESSION,
            group,
            "the concurrency group must key on the branch this job pushes to, "
            "so two reseals of one branch serialize",
        )
        self.assertNotIn(
            "github.ref }}",
            group,
            "github.ref is refs/pull/N/merge on pull-request events and "
            "refs/heads/... on push events; keying on it is what let the two "
            "event families occupy different groups",
        )

    def test_concurrency_does_not_cancel_a_push_in_flight(self) -> None:
        """A cancelled reseal can drop a commit it already pushed.

        Queueing is correct here; cancelling mid-push is not.
        """
        self.assertIs(False, self.workflow["concurrency"]["cancel-in-progress"])

    def test_one_branch_expression_is_shared_by_every_write_surface(self) -> None:
        """Checkout ref, concurrency key, and push target must be one branch.

        If the checkout ref and the push target can diverge, the job can
        reseal one tree and push the result onto another branch.
        """
        checkout = next(
            step
            for step in self.job["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertEqual(
            f"${{{{ {BRANCH_EXPRESSION} }}}}",
            checkout["with"]["ref"],
        )

        reseal = next(
            step for step in self.job["steps"] if "run" in step and "env" in step
        )
        self.assertEqual(
            {"RESEAL_BRANCH": f"${{{{ {BRANCH_EXPRESSION} }}}}"},
            reseal["env"],
        )
        self.assertIn('git push origin "HEAD:${RESEAL_BRANCH}"', reseal["run"])

        # github.head_ref is empty outside pull-request events. Leaving a
        # `head_ref || ref_name` fallback in place would be dead code that
        # silently reintroduces pull-request semantics if the trigger returns.
        self.assertNotIn("github.head_ref", self.directives)

    # --- authorization ---------------------------------------------------

    def test_runs_only_for_dependabot(self) -> None:
        self.assertEqual("github.actor == 'dependabot[bot]'", self.job["if"])

    def test_write_permission_is_job_scoped_and_minimal(self) -> None:
        """Workflow-level read, job-level write, contents only."""
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        self.assertEqual({"contents": "write"}, self.job["permissions"])

    def test_checkout_is_sha_pinned(self) -> None:
        self.assertRegex(self.directives, r"uses:\s*actions/checkout@[0-9a-f]{40}")

    # --- bounded mutation ------------------------------------------------

    def test_mutation_is_bounded_to_the_manifest(self) -> None:
        reseal = next(
            step for step in self.job["steps"] if "run" in step and "env" in step
        )
        run = reseal["run"]
        self.assertIn("python3 -m tools.l9_repo reseal-manifest", run)
        self.assertIn("git add -- MANIFEST.sha256", run)
        self.assertIn("git diff --cached --quiet -- MANIFEST.sha256", run)

        staged = re.findall(r"^\s*git add\b.*$", run, flags=re.MULTILINE)
        self.assertEqual(
            ["git add -- MANIFEST.sha256"],
            [line.strip() for line in staged],
            "the reseal job may stage MANIFEST.sha256 and nothing else",
        )

    def test_history_is_never_rewritten_and_hooks_are_never_skipped(self) -> None:
        for forbidden in ("--no-verify", "git push -f", "git push --force"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.directives)


if __name__ == "__main__":
    unittest.main()
