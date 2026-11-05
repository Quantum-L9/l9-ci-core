"""Core's own workflows must satisfy the fail-closed integrity checker.

``make validate`` runs the same checker, but this test makes the guarantee
part of the workflow test suite so any future fail-open regression is caught
by targeted workflow gates as well.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_integrity_module():
    """Load the checker directly from its file so discovery mode
    (``unittest discover --start-directory tests``) works without
    requiring the repository root on ``sys.path``."""
    spec = importlib.util.spec_from_file_location(
        "check_workflow_integrity",
        ROOT / "tools" / "check_workflow_integrity.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


integrity = _load_integrity_module()


class WorkflowIntegrityCleanTests(unittest.TestCase):
    def test_all_core_workflows_and_actions_are_fail_closed(self) -> None:
        workflows = sorted((ROOT / ".github/workflows").rglob("*.yml")) + sorted(
            (ROOT / ".github/workflows").rglob("*.yaml")
        )
        actions = sorted((ROOT / ".github/actions").rglob("*.yml")) + sorted(
            (ROOT / ".github/actions").rglob("*.yaml")
        )
        self.assertTrue(workflows, "no workflows found")
        self.assertTrue(actions, "no composite actions found")
        violations: list[str] = []
        for workflow in workflows:
            violations.extend(integrity.check_file(workflow))
        for action in actions:
            violations.extend(integrity.check_file(action, composite=True))
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
