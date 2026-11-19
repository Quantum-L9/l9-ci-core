"""The required job does not verify an unadmitted organization policy.

``.github`` at ``9d68f33220477f48ea427910d59c05848e37d311`` says the
candidate is not admitted. ``policies/verify_release.py --require-release``
rejects a subject when ``--admission`` is absent, including a valid
signature. The required workflow does not call that verifier. This test
does not supply an admission file and does not change ``--require-release``.
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


class OrgPolicyPinTests(unittest.TestCase):
    def test_required_job_does_not_call_the_unadmitted_verifier(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        job = text[text.index("name: Analyze (central Core)") :]
        self.assertNotIn("id: org-policy", job)
        self.assertNotIn("verify_release.py", job)
        self.assertNotIn("--require-release", job)
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
