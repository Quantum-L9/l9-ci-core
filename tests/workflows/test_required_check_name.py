"""The required check name is written once, in Core's runtime contract.

``.l9/org-runtime-contract.yaml`` ``entrypoint.required_check.name`` is the one
source. ``org-ci.yml``'s job carries it, and nothing else in Core's live tree
writes the literal: tests, tools and documents reference the contract key. A
rename then changes the contract value, the job name and the organization
ruleset entry, and nothing else.

Historical evidence keeps the name it had when it was written: the dated
sections of ``AGENTS.md``, reports, and the two recorded 2026-09-13 /
2026-10-22 observations listed in ``HISTORICAL`` below.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / ".l9" / "org-runtime-contract.yaml"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"

# Files that may carry the literal: the two that define it.
DEFINING = {".l9/org-runtime-contract.yaml", ".github/workflows/org-ci.yml"}
# Records of what was observed or decided under an earlier name.
HISTORICAL_PREFIXES = ("reports/", "docs/reports/")
HISTORICAL = {
    "AGENTS.md",
    ".l9/release-plane.yaml",
    "docs/release/README.md",
    "docs/release/rulesets/README.md",
    "tests/workflows/test_required_check_name.py",
}
ANY_SPELLING = re.compile(r"Analyze \((?i:central core)\)")


def contract_name() -> str:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return contract["entrypoint"]["required_check"]["name"]


class RequiredCheckNameTests(unittest.TestCase):
    def test_the_contract_names_the_check_and_its_job(self) -> None:
        contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
        required = contract["entrypoint"]["required_check"]
        self.assertEqual("Analyze (central Core)", required["name"])
        self.assertEqual("analyze", required["job"])

    def test_org_ci_job_carries_exactly_the_contract_name(self) -> None:
        workflow = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
        job = contract["entrypoint"]["required_check"]["job"]
        self.assertEqual(contract_name(), workflow["jobs"][job]["name"])

    def test_the_push_caller_cannot_reuse_the_required_check_name(self) -> None:
        caller_path = ROOT / ".github" / "workflows" / "org-ci-default-branch-push.yml"
        caller = yaml.safe_load(caller_path.read_text(encoding="utf-8"))
        required = contract_name()
        self.assertNotEqual(required, caller.get("name"))
        triggers = caller[True] if True in caller else caller["on"]
        self.assertIn("push", triggers)
        self.assertNotIn("pull_request", triggers)
        for job_id, job in caller["jobs"].items():
            self.assertNotEqual(required, job.get("name"), job_id)
            self.assertNotIn("Analyze", job.get("name", ""))
        org = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        org_on = org[True] if True in org else org["on"]
        self.assertNotIn("push", org_on)

    def test_no_live_file_writes_the_literal(self) -> None:
        tracked = (
            subprocess.run(
                ["git", "-C", str(ROOT), "ls-files", "-z"],
                capture_output=True,
                check=True,
            )
            .stdout.decode("utf-8", "surrogateescape")
            .split("\0")
        )
        offenders = []
        for relative in filter(None, tracked):
            if relative in DEFINING or relative in HISTORICAL:
                continue
            if relative.startswith(HISTORICAL_PREFIXES):
                continue
            path = ROOT / relative
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if b"\0" in data[:8192]:
                continue
            for number, line in enumerate(
                data.decode("utf-8", "replace").splitlines(), start=1
            ):
                if ANY_SPELLING.search(line):
                    offenders.append(f"{relative}:{number}")
        self.assertEqual(
            [],
            offenders,
            "reference .l9/org-runtime-contract.yaml entrypoint.required_check.name "
            "instead of writing the check name",
        )


if __name__ == "__main__":
    unittest.main()
