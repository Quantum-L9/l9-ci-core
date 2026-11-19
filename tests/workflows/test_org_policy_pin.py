"""The candidate required workflow does not verify an unadmitted policy.

``Analyze (central Core)`` on a pull request runs ``org-ci.yml`` from
``main``, so a green required check does not execute this branch's
workflow. This module reads the candidate file. ``.github`` pull request
269 is still unmerged. ``verify_release.py`` needs both a verified
signature and an owner admission, and this workflow supplies neither.
The call stays off the required job until an admitted release exists.
``--require-release`` is not weakened and no admission file is added.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "org-ci.yml"
SELF_CI = ROOT / ".github" / "workflows" / "self-ci.yml"
GATING = ROOT / "tests" / "workflows" / "test_self_ci_gating.py"
RESOLVE = ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
DEFAULTS = ROOT / ".github" / "actions" / "resolve-governance" / "defaults"
MODULE = "tests/workflows/test_org_policy_pin.py"


class OrgPolicyPinTests(unittest.TestCase):
    def test_candidate_workflow_does_not_call_the_unadmitted_verifier(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        job = text[text.index("name: Analyze (central Core)") :]
        self.assertNotIn("id: org-policy", job)
        self.assertNotIn("verify_release.py", job)
        self.assertNotIn("--require-release", job)
        self.assertNotIn("--admission", job)
        self.assertNotIn("admission-trust.yaml", job)
        self.assertNotIn("continue-on-error", job)

    def test_module_is_on_exactly_the_pr_unit_list(self) -> None:
        self.assertIn(MODULE, SELF_CI.read_text(encoding="utf-8"))
        self.assertIn(f'"{MODULE}"', GATING.read_text(encoding="utf-8"))
        repo = (ROOT / "Repo.mk").read_text(encoding="utf-8")
        block = repo.split("REPO_TEST_MODULES :=", 1)[1].split("repo-test:", 1)[0]
        self.assertNotIn(MODULE, block)

    def test_defaults_registry_stays_six_files(self) -> None:
        source = RESOLVE.read_text(encoding="utf-8")
        block = re.search(r"EXPECTED_SCHEMAS = \{([^}]+)\}", source)
        self.assertIsNotNone(block)
        assert block is not None
        names = re.findall(r'"([^"]+)"\s*:', block.group(1))
        self.assertEqual(6, len(names))
        on_disk = {path.name for path in DEFAULTS.iterdir() if path.is_file()}
        self.assertEqual(set(names), on_disk)
