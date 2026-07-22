from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

_WRITE_SCOPE = re.compile(
    r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
    r"id-token|issues|packages|pages|pull-requests|"
    r"repository-projects|security-events|statuses):\s+write"
)
_PUBLISH_CALL = re.compile(
    r"(?m)^\s*uses:\s*\S*(?:publish-analysis|analyze-semgrep)"
    r"\.yml(?:@[0-9a-fA-F]{40})?\s*$"
)


class WorkflowPermissionTests(unittest.TestCase):
    def test_every_workflow_declares_read_only_contents(self) -> None:
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))

    def test_checks_write_only_where_a_check_is_published(self) -> None:
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                scopes = set(_WRITE_SCOPE.findall(text))
                publishes = (
                    workflow.name == "publish-analysis.yml"
                    or _PUBLISH_CALL.search(text) is not None
                )
                if "checks" in scopes:
                    self.assertTrue(
                        publishes,
                        f"{workflow.name} requests checks:write but does not "
                        "publish via publish-analysis.yml",
                    )
                self.assertEqual(
                    set(),
                    scopes - {"checks"},
                    f"{workflow.name} requests forbidden write scopes: "
                    f"{sorted(scopes - {'checks'})}",
                )


if __name__ == "__main__":
    unittest.main()
