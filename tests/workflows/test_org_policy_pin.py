"""The required job verifies the pinned organization policy and fails closed.

``.github`` at ``fe4084ca80909f2a40e78bf8e5bc89a54753f45b`` loads the sibling
admission and trust root. It does not accept an ``--admission`` path. The
required workflow calls that verifier with ``--require-release`` and does
not continue on error. A live verified result is still impossible: the
trust root is empty and no owner admission exists.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "org-ci.yml"
SELF_CI = ROOT / ".github" / "workflows" / "self-ci.yml"
RESOLVE = ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
DEFAULTS = ROOT / ".github" / "actions" / "resolve-governance" / "defaults"
MODULE = "tests/workflows/test_org_policy_pin.py"
POLICY_SHA = "fe4084ca80909f2a40e78bf8e5bc89a54753f45b"


class OrgPolicyPinTests(unittest.TestCase):
    def test_required_job_verifies_the_pinned_policy(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        job = text[text.index("name: Analyze (central Core)") :]
        self.assertIn("id: org-policy", job)
        self.assertIn("verify_release.py", job)
        self.assertIn("--require-release", job)
        self.assertIn(POLICY_SHA, job)
        self.assertIn("policies/admission-trust.yaml", job)
        self.assertNotIn("--admission", job)
        self.assertNotIn("continue-on-error", job)

    def test_module_is_on_the_pr_unit_list(self) -> None:
        self.assertIn(MODULE, SELF_CI.read_text(encoding="utf-8"))

    def test_defaults_registry_stays_six_files(self) -> None:
        source = RESOLVE.read_text(encoding="utf-8")
        block = re.search(r"EXPECTED_SCHEMAS = \{([^}]+)\}", source)
        self.assertIsNotNone(block)
        assert block is not None
        names = re.findall(r'"([^"]+)"\s*:', block.group(1))
        self.assertEqual(6, len(names))
        on_disk = {path.name for path in DEFAULTS.iterdir() if path.is_file()}
        self.assertEqual(set(names), on_disk)
