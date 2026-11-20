"""Structure of the Core-hosted settings runner (``settings-runner.yml``).

GitHub settings as code (campaign L9-GITHUB-PRIVATE-01, lane R): the workflow
is reusable only and scheduled by the repository that holds the repo-birth
App credential (no new App, secret or environment); Core is checked out at the
exact commit the caller pins; GITHUB_TOKEN stays read-only; the declaration
is read with a contents:read token scoped to Quantum-L9/.github alone; the
settings token is scoped to exactly the declared repositories; dry run is the
default and is the only path that cannot write.
"""

from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "settings-runner.yml"
APP_TOKEN = "actions/create-github-app-token@"


def load() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    document["on"] = document.pop(True)
    return document


class SettingsRunnerWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = load()
        self.job = self.workflow["jobs"]["settings"]
        self.steps = self.job["steps"]

    def step(self, step_id: str) -> dict:
        return next(s for s in self.steps if s.get("id") == step_id)

    def test_trigger_is_workflow_call_only(self) -> None:
        self.assertEqual({"workflow_call"}, set(self.workflow["on"]))
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            {"repository", "dry-run", "core-ref", "environment"}, set(inputs)
        )
        self.assertIs(True, inputs["dry-run"]["default"])
        self.assertEqual("boolean", inputs["dry-run"]["type"])
        self.assertIs(True, inputs["core-ref"]["required"])
        self.assertEqual("repo-birth", inputs["environment"]["default"])
        secrets = self.workflow["on"]["workflow_call"]["secrets"]
        self.assertEqual({"BIRTH_APP_PRIVATE_KEY"}, set(secrets))

    def test_github_token_is_read_only(self) -> None:
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        self.assertEqual({"contents": "read"}, self.job["permissions"])

    def test_job_reads_caller_environment(self) -> None:
        self.assertEqual("${{ inputs.environment }}", self.job["environment"])
        self.assertIs(False, self.job["concurrency"]["cancel-in-progress"])

    def test_core_is_the_pinned_commit(self) -> None:
        first = self.steps[0]
        self.assertEqual("Quantum-L9/l9-ci-core", first["with"]["repository"])
        self.assertEqual("${{ inputs.core-ref }}", first["with"]["ref"])
        refuse = self.steps[1]
        self.assertIn("git rev-parse HEAD", refuse["run"])
        self.assertIn("[0-9a-f]{40}", refuse["run"])
        # The caller-supplied core-ref is bound to the commit GitHub resolved
        # this reusable workflow from, not merely to the checkout it chose.
        self.assertEqual("${{ job.workflow_sha }}", refuse["env"]["WORKFLOW_SHA"])
        self.assertIn('[ "${WORKFLOW_SHA}" = "${CORE_REF}" ]', refuse["run"])

    def test_declaration_token_is_contents_read_on_dotgithub_alone(self) -> None:
        token = self.step("declaration-token")
        self.assertTrue(token["uses"].startswith(APP_TOKEN))
        self.assertEqual(".github", token["with"]["repositories"])
        permissions = {k for k in token["with"] if k.startswith("permission-")}
        self.assertEqual({"permission-contents"}, permissions)
        self.assertEqual("read", token["with"]["permission-contents"])
        checkout = next(
            s for s in self.steps if s.get("with", {}).get("path") == "dotgithub"
        )
        self.assertEqual("main", checkout["with"]["ref"])
        self.assertIs(False, checkout["with"]["persist-credentials"])

    def test_settings_token_is_scoped_to_declared_repositories(self) -> None:
        token = self.step("app")
        self.assertEqual(
            "${{ steps.declared.outputs.repositories }}", token["with"]["repositories"]
        )
        self.assertEqual(
            {
                "permission-administration": "write",
                "permission-actions": "read",
                "permission-metadata": "read",
            },
            {k: v for k, v in token["with"].items() if k.startswith("permission-")},
        )
        for step in self.steps:
            if str(step.get("uses", "")).startswith(APP_TOKEN):
                self.assertEqual(
                    "${{ secrets.BIRTH_APP_PRIVATE_KEY }}", step["with"]["private-key"]
                )
                self.assertEqual("${{ vars.BIRTH_APP_ID }}", step["with"]["app-id"])

    def test_writes_only_when_dry_run_is_false(self) -> None:
        final = self.steps[-1]
        self.assertEqual(
            "${{ inputs.dry-run && 'plan' || 'apply' }}", final["env"]["STAGE"]
        )
        self.assertIn("python3 -m tools.l9_settings", final["run"])
        self.assertEqual("${{ steps.app.outputs.token }}", final["env"]["GH_TOKEN"])
        # The settings token reaches no other step.
        users = [s for s in self.steps if "steps.app.outputs.token" in str(s)]
        self.assertEqual([final], users)

    def test_no_fail_open_construct(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error", text)
        self.assertNotIn("secrets: inherit", text)


if __name__ == "__main__":
    unittest.main()
