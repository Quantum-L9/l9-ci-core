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

    def test_only_publication_workflow_requests_checks_write(self) -> None:
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                matches = write_pattern.findall(text)
                if workflow.name == "publish-analysis.yml":
                    self.assertEqual(["checks"], matches)
                else:
                    self.assertEqual([], matches)


if __name__ == "__main__":
    unittest.main()
