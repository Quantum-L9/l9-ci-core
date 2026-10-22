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
    #   publish-analysis.yml    checks:write only; publishes one elected check.
    #   publish-sarif.yml       security-events:write only; uploads one elected
    #                           SDK SARIF projection.
    #   self-analysis.yml       trusted self-only caller granting checks:write
    #                           to its explicit check publication job.
    #
    # `org-ci.yml` is intentionally absent. The organization required workflow
    # runs on untrusted pull_request events and must remain contents:read only.
    WRITE_EXCEPTIONS = {
        "publish-analysis.yml": ["checks"],
        "publish-sarif.yml": ["security-events"],
        "self-analysis.yml": ["checks"],
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

    def test_no_workflow_references_the_sdk_biome_workflow(self) -> None:
        """Core owns the consumer-facing Biome check (AGENTS-ADDENDUM.md).

        The copy-first preset and starter callers of
        ``l9-ci-sdk/.github/workflows/l9-biome-scan.yml`` were retired on
        2026-10-22; no Core workflow may reintroduce that dependency edge.
        """
        # GitHub loads a workflow from either spelling (AGENTS.md §12).
        for workflow in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
            with self.subTest(workflow=workflow.name):
                self.assertNotIn("l9-biome-scan", workflow.read_text(encoding="utf-8"))

    def test_write_scoped_workflows_are_not_pull_request_triggered(self) -> None:
        trigger_pattern = re.compile(r"(?m)^\s*(pull_request|pull_request_target):")
        reusable = {
            "publish-analysis.yml",
            "publish-sarif.yml",
            "analyze-semgrep.yml",
        }
        audited_pr_callers = {"self-analysis.yml"}
        for name in self.WRITE_EXCEPTIONS:
            workflow = WORKFLOWS / name
            if name in reusable or name in audited_pr_callers:
                continue
            with self.subTest(workflow=name):
                text = workflow.read_text(encoding="utf-8")
                self.assertIsNone(
                    trigger_pattern.search(text),
                    f"{name} requests write permissions and must not be "
                    "triggered by pull_request events",
                )

    def test_org_ci_required_workflow_is_pull_request_safe(self) -> None:
        text = (WORKFLOWS / "org-ci.yml").read_text(encoding="utf-8")
        self.assertRegex(text, re.compile(r"(?m)^\s*pull_request:\s*$"))
        self.assertRegex(text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        self.assertEqual([], write_pattern.findall(text))


if __name__ == "__main__":
    unittest.main()
