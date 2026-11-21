"""Every step of the required job has a class in the blocking-check table.

``docs/blocking-check-classification.md`` classifies each ``org-ci.yml``
``analyze`` step as correctness, security, housekeeping, plumbing or pending.
A step added, renamed or removed without a row fails here, and a step marked
HOUSEKEEPING must carry "(advisory)" in its name: housekeeping never blocks.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "blocking-check-classification.md"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
CLASSES = {
    "BLOCKING_CORRECTNESS",
    "BLOCKING_SECURITY",
    "HOUSEKEEPING",
    "PLUMBING",
    "PENDING",
    "SPLIT",
}
ROW = re.compile(r"^\| (?P<step>[^|]+?) \| (?P<cls>[A-Z_]+) \|")


def step_names() -> list[str]:
    workflow = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    return [
        str(step.get("name") or step.get("id"))
        for step in workflow["jobs"]["analyze"]["steps"]
    ]


def classified() -> dict[str, str]:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Repository verification", 1)[0]
    rows = {}
    for line in section.splitlines():
        match = ROW.match(line)
        if match and match["cls"] in CLASSES:
            rows[match["step"]] = match["cls"]
    return rows


class BlockingCheckClassificationTests(unittest.TestCase):
    def test_every_step_has_exactly_one_row(self) -> None:
        self.assertEqual(sorted(step_names()), sorted(classified()))

    def test_housekeeping_steps_are_named_advisory(self) -> None:
        for step, cls in classified().items():
            if cls == "HOUSEKEEPING":
                self.assertTrue(step.endswith("(advisory)"), step)


if __name__ == "__main__":
    unittest.main()
