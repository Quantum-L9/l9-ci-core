"""Core's own workflows must satisfy the fail-closed integrity checker.

Locks in the remediation of the last fail-open constructs (`|| true`
suffixes guarding whole-command outcomes in ``nightly.yml`` and
``trio-governance.yml``). ``make validate`` runs the same checker, but this
test makes the guarantee part of the workflow test suite so any future
fail-open regression is caught by targeted workflow gates as well.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_check_file():
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
    return module.check_file


check_file = _load_check_file()


class WorkflowIntegrityCleanTests(unittest.TestCase):
    def test_all_core_workflows_are_fail_closed(self) -> None:
        workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
        self.assertTrue(workflows, "no workflows found")
        violations: list[str] = []
        for workflow in workflows:
            violations.extend(check_file(workflow))
        self.assertEqual([], violations)

    def test_freshness_reports_remain_informational(self) -> None:
        """The nightly freshness reports must keep never-fail semantics
        via checker-exempt command-substitution capture, not bare
        fail-open suffixes."""
        nightly = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")
        self.assertIn('report="$(pip list --outdated || true)"', nightly)
        self.assertIn('report="$(npm outdated || true)"', nightly)


if __name__ == "__main__":
    unittest.main()
