"""A caller may only read outputs its local reusable workflow declares.

GitHub resolves ``needs.<job>.outputs.<name>`` for a reusable-workflow job
from the callee's ``on.workflow_call.outputs``; a job-level output that is not
re-exported there evaluates to an empty string without any error. An
undeclared read therefore fails silently downstream, as an empty
``governance-digest`` once did in the terminal publication.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
LOCAL_CALL = re.compile(r"^\./\.github/workflows/([A-Za-z0-9._-]+\.ya?ml)$")
NEEDS_OUTPUT = re.compile(
    r"needs\.([A-Za-z0-9_-]+)\.outputs(?:\.([A-Za-z0-9_-]+)|\[['\"]([A-Za-z0-9_-]+)['\"]\])"
)


def load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError(f"{path.name}: workflow is not a mapping")
    return document


def declared_outputs(callee: dict) -> set[str]:
    # PyYAML reads the bare `on` key as boolean True.
    triggers = callee.get("on", callee.get(True)) or {}
    call = triggers.get("workflow_call") if isinstance(triggers, dict) else None
    return set((call or {}).get("outputs") or {})


def undeclared_reads(caller: dict) -> list[str]:
    jobs = caller.get("jobs") or {}
    callees: dict[str, set[str]] = {}
    for job_id, job in jobs.items():
        match = LOCAL_CALL.match(str((job or {}).get("uses", "")))
        if match:
            callees[job_id] = declared_outputs(load(WORKFLOWS / match.group(1)))
    problems: list[str] = []
    for job_id, job in jobs.items():
        text = yaml.safe_dump(job)
        for match in NEEDS_OUTPUT.finditer(text):
            needed, name = match.group(1), match.group(2) or match.group(3)
            if needed in callees and name not in callees[needed]:
                problems.append(f"{job_id} reads needs.{needed}.outputs.{name}")
    return sorted(set(problems))


class ReusableWorkflowOutputTests(unittest.TestCase):
    def test_every_read_reusable_output_is_declared_by_the_callee(self) -> None:
        workflows = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(workflows)
        for path in workflows:
            with self.subTest(workflow=path.name):
                self.assertEqual([], undeclared_reads(load(path)))

    def test_self_analysis_publication_receives_the_governance_digest(self) -> None:
        callee = load(WORKFLOWS / "analyze-semgrep.yml")
        self.assertIn("governance-digest", declared_outputs(callee))

    def test_an_undeclared_read_is_detected(self) -> None:
        caller = {
            "jobs": {
                "analyze": {"uses": "./.github/workflows/analyze-semgrep.yml"},
                "publish": {
                    "needs": "analyze",
                    "with": {"x": "${{ needs.analyze.outputs.not-declared }}"},
                },
            }
        }
        self.assertEqual(
            ["publish reads needs.analyze.outputs.not-declared"],
            undeclared_reads(caller),
        )


if __name__ == "__main__":
    unittest.main()
