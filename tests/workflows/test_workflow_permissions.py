from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowPermissionTests(unittest.TestCase):
    def test_every_workflow_declares_read_only_contents(self) -> None:
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))

    # Write permissions are read-only everywhere except these audited
    # exceptions. Any change to this table is a trust-boundary change.
    #
    #   publish-analysis.yml    checks:write — publishes the GitHub check run.
    #   analyze-semgrep.yml     checks:write — its `publish` job grants the
    #                           nested publish-analysis.yml call the check-run
    #                           scope. workflow_call-only; the caller gates
    #                           the trigger surface.
    #   regenerate-identity-maps.yml
    #                           contents/pull-requests:write — opens a PR when
    #                           the semgrep registry drifts. Confined to its
    #                           `regenerate` job and unreachable from untrusted
    #                           events (enforced by the trigger test below).
    WRITE_EXCEPTIONS = {
        "publish-analysis.yml": ["checks"],
        "analyze-semgrep.yml": ["checks"],
        "regenerate-identity-maps.yml": ["contents", "pull-requests"],
    }

    def test_only_authorized_workflows_request_write(self) -> None:
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                matches = write_pattern.findall(text)
                expected = self.WRITE_EXCEPTIONS.get(workflow.name, [])
                self.assertEqual(expected, matches)

    def test_write_scoped_workflows_are_not_pull_request_triggered(self) -> None:
        # A workflow that can obtain write permissions must never run on
        # untrusted `pull_request` events, or a fork PR could reach that scope.
        # publish-analysis.yml and analyze-semgrep.yml are reusable
        # (workflow_call) and gated by the caller; the maintenance workflow
        # must be schedule/dispatch only.
        trigger_pattern = re.compile(r"(?m)^\s*(pull_request|pull_request_target):")
        reusable = {"publish-analysis.yml", "analyze-semgrep.yml"}
        for name in self.WRITE_EXCEPTIONS:
            workflow = WORKFLOWS / name
            if name in reusable:
                continue
            with self.subTest(workflow=name):
                text = workflow.read_text(encoding="utf-8")
                self.assertIsNone(
                    trigger_pattern.search(text),
                    f"{name} requests write permissions and must not be "
                    "triggered by pull_request events",
                )


if __name__ == "__main__":
    unittest.main()
