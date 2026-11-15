"""Structure of the Core-hosted release runner (``release-runner.yml``).

Locks the operator decisions of 2026-11-15: the engine and this workflow live
in Core, never inside the org-CI required workflow; the workflow is reusable
(``workflow_call`` only) and is scheduled by the repository that already holds
the repo-birth App credential, whose ``repo-birth`` environment supplies it (no
new App, secret or environment); Core is checked out at the exact commit the
caller pins; GITHUB_TOKEN stays read-only; every write uses a per-repository
App installation token; one repository's failure never blocks another.
"""

from __future__ import annotations

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release-runner.yml"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
APP_TOKEN = "actions/create-github-app-token@"


def load() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare `on` key as boolean True.
    document["on"] = document.pop(True)
    return document


def app_steps(job: dict) -> list[dict]:
    return [
        step for step in job["steps"] if str(step.get("uses", "")).startswith(APP_TOKEN)
    ]


class ReleaseRunnerWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = load()
        self.jobs = self.workflow["jobs"]

    def test_trigger_is_workflow_call_only(self) -> None:
        # Core holds no release credential, so a Core-side schedule or
        # dispatch could only fail; the caller owns the schedule.
        self.assertEqual({"workflow_call"}, set(self.workflow["on"]))
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual({"repository", "core-ref", "environment"}, set(inputs))
        self.assertIs(True, inputs["core-ref"]["required"])
        self.assertEqual("", inputs["repository"]["default"])
        self.assertEqual("repo-birth", inputs["environment"]["default"])
        self.assertNotIn("secrets", self.workflow["on"]["workflow_call"])

    def test_github_context_is_never_read_as_core_identity(self) -> None:
        # In a reusable workflow the github context is the caller's: github.sha
        # is the caller's commit and github.event_name the caller's event.
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("github.sha", text)
        self.assertNotIn("github.event_name", text)
        self.assertNotIn("github.workflow_sha", text)

    def test_core_is_checked_out_at_the_pinned_commit_and_verified(self) -> None:
        for name in ("discover", "release"):
            steps = self.jobs[name]["steps"]
            checkouts = [
                s
                for s in steps
                if str(s.get("uses", "")).startswith("actions/checkout@")
            ]
            with self.subTest(job=name):
                self.assertEqual(1, len(checkouts))
                self.assertEqual(
                    "Quantum-L9/l9-ci-core", checkouts[0]["with"]["repository"]
                )
                self.assertEqual("${{ inputs.core-ref }}", checkouts[0]["with"]["ref"])
                guard = steps[steps.index(checkouts[0]) + 1]
                self.assertEqual("${{ inputs.core-ref }}", guard["env"]["CORE_REF"])
                self.assertIn("^[0-9a-f]{40}$", guard["run"])
                self.assertIn('"$(git rev-parse HEAD)" = "${CORE_REF}"', guard["run"])

    def test_engine_revision_is_the_pinned_core_commit(self) -> None:
        envs = [
            step.get("env", {})
            for step in self.jobs["release"]["steps"]
            if "L9_RELEASE_ENGINE_REVISION" in step.get("env", {})
        ]
        self.assertEqual(2, len(envs))
        for env in envs:
            self.assertEqual(
                "${{ inputs.core-ref }}", env["L9_RELEASE_ENGINE_REVISION"]
            )

    def test_one_repository_release_is_strict_whatever_the_caller_event(self) -> None:
        steps = {s.get("id"): s for s in self.jobs["discover"]["steps"]}
        self.assertEqual(
            "${{ inputs.repository }}", steps["members"]["env"]["DISPATCHED"]
        )
        self.assertEqual(
            "${{ inputs.repository != '' }}", steps["discover"]["env"]["STRICT"]
        )

    def test_github_token_is_read_only(self) -> None:
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(
            [],
            re.findall(
                r"(?m)^\s+[a-z-]+:\s+write\s*$",
                text.replace("permission-contents: write", ""),
            ),
        )

    def test_credentials_only_from_the_caller_environment(self) -> None:
        for name, job in self.jobs.items():
            dumped = yaml.safe_dump(job)
            credentialed = "secrets." in dumped or "vars." in dumped
            with self.subTest(job=name):
                if credentialed:
                    self.assertEqual(
                        "${{ inputs.environment }}", job.get("environment")
                    )
        text = WORKFLOW.read_text(encoding="utf-8")
        # The existing repo-birth credential names; no release-only credential.
        self.assertEqual(
            {"secrets.BIRTH_APP_PRIVATE_KEY"},
            set(re.findall(r"secrets\.[A-Z_]+", text)),
        )
        self.assertEqual({"vars.BIRTH_APP_ID"}, set(re.findall(r"vars\.[A-Z_]+", text)))
        self.assertNotIn("release-runner", yaml.safe_dump(self.jobs))
        for name in ("discover", "release"):
            (step,) = app_steps(self.jobs[name])
            with self.subTest(job=name):
                self.assertEqual("${{ vars.BIRTH_APP_ID }}", step["with"]["app-id"])
                self.assertEqual(
                    "${{ secrets.BIRTH_APP_PRIVATE_KEY }}", step["with"]["private-key"]
                )

    def test_discovery_token_is_scoped_to_registry_members_read_only(self) -> None:
        (step,) = app_steps(self.jobs["discover"])
        self.assertEqual(
            "${{ steps.members.outputs.repositories }}", step["with"]["repositories"]
        )
        self.assertEqual("read", step["with"]["permission-contents"])

    def test_release_token_is_one_repository_contents_write(self) -> None:
        (step,) = app_steps(self.jobs["release"])
        self.assertEqual("${{ matrix.name }}", step["with"]["repositories"])
        self.assertEqual("write", step["with"]["permission-contents"])
        self.assertEqual(
            {
                "app-id",
                "private-key",
                "owner",
                "repositories",
                "permission-contents",
            },
            set(step["with"]),
        )

    def test_failure_isolation_and_per_repository_concurrency(self) -> None:
        release = self.jobs["release"]
        self.assertIs(False, release["strategy"]["fail-fast"])
        self.assertEqual(
            "l9-release-${{ matrix.repository }}", release["concurrency"]["group"]
        )
        self.assertIs(False, release["concurrency"]["cancel-in-progress"])
        report = self.jobs["report"]
        self.assertEqual("always()", report["if"])
        self.assertEqual({}, report["permissions"])

    def test_release_runs_build_with_phases_then_publish(self) -> None:
        runs = "\n".join(
            str(step.get("run", "")) for step in self.jobs["release"]["steps"]
        )
        self.assertIn("plan", runs)
        self.assertIn("--phases run", runs)
        self.assertIn("publish --dist", runs)
        self.assertNotIn("${{", runs)  # expressions arrive through env only

    def test_org_ci_carries_no_release_job(self) -> None:
        text = ORG_CI.read_text(encoding="utf-8")
        self.assertNotIn("release-engine", text)
        self.assertNotIn("l9_release", text)


if __name__ == "__main__":
    unittest.main()
