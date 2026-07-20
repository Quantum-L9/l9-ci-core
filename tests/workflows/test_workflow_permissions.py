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
_PUBLISH_CALL = "uses: ./.github/workflows/publish-analysis.yml"


class WorkflowPermissionTests(unittest.TestCase):
    def test_every_workflow_declares_read_only_contents(self) -> None:
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))

    def test_checks_write_only_where_a_check_is_published(self) -> None:
        # Capability reservation (not name reservation): checks:write is allowed
        # ONLY in the publication workflow itself OR in a workflow that publishes
        # through it (a job calling publish-analysis.yml needs to pass checks:write
        # to the reusable workflow). No other write scope is permitted anywhere.
        for workflow in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                scopes = set(_WRITE_SCOPE.findall(text))
                publishes = (
                    workflow.name == "publish-analysis.yml" or _PUBLISH_CALL in text
                )
                if "checks" in scopes:
                    self.assertTrue(
                        publishes,
                        f"{workflow.name} requests checks:write but does not "
                        f"publish via publish-analysis.yml",
                    )
                # checks is the ONLY write scope ever permitted; everything else
                # (contents:write, id-token:write, ...) stays forbidden.
                self.assertEqual(
                    set(),
                    scopes - {"checks"},
                    f"{workflow.name} requests forbidden write scopes: "
                    f"{sorted(scopes - {'checks'})}",
                )


if __name__ == "__main__":
    unittest.main()
