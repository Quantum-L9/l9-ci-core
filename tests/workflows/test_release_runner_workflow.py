"""Structure of the Core-hosted release runner (``release-runner.yml``).

Locks the operator decision of 2026-11-15: releases run in Core on a daily
schedule plus a dispatch for one repository, never inside the org-CI required
workflow; GITHUB_TOKEN stays read-only; every write uses a per-repository App
installation token from the ``release-runner`` environment; one repository's
failure never blocks another.
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

    def test_triggers_are_schedule_and_dispatch_only(self) -> None:
        self.assertEqual({"schedule", "workflow_dispatch"}, set(self.workflow["on"]))
        self.assertEqual(1, len(self.workflow["on"]["schedule"]))
        self.assertEqual(
            {"repository"}, set(self.workflow["on"]["workflow_dispatch"]["inputs"])
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

    def test_secrets_only_in_the_release_runner_environment(self) -> None:
        for name, job in self.jobs.items():
            uses_secrets = "secrets." in yaml.safe_dump(job)
            with self.subTest(job=name):
                if uses_secrets:
                    self.assertEqual("release-runner", job.get("environment"))

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
                "client-id",
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
