"""The required Core job verifies the organization-policy release.

Valid means a checked Sigstore signature from the default-branch provenance
workflow plus an owner admission of the same digest. Those inputs are decided
by ``Quantum-L9/.github`` ``policies/verify_release.py``. This required job
calls that verifier and fails closed unless it returns verified. A named
signer string is not verification.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "org-ci.yml"
RESOLVE = ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
DEFAULTS = ROOT / ".github" / "actions" / "resolve-governance" / "defaults"
PIN = "9d68f33220477f48ea427910d59c05848e37d311"


class OrgPolicyPinTests(unittest.TestCase):
    def test_required_job_verifies_the_upstream_release(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        start = text.index("name: Analyze (central Core)")
        job = text[start:]
        step = job.index("id: org-policy")
        window = job[step : step + 500]
        self.assertIn("policies/verify_release.py", window)
        self.assertIn("--require-release", window)
        self.assertIn(PIN, job)
        self.assertNotIn("continue-on-error", window)

    def test_defaults_registry_stays_six_files(self) -> None:
        source = RESOLVE.read_text(encoding="utf-8")
        block = re.search(r"EXPECTED_SCHEMAS = \{([^}]+)\}", source)
        self.assertIsNotNone(block)
        assert block is not None
        names = re.findall(r'"([^"]+)"\s*:', block.group(1))
        self.assertEqual(6, len(names))
        on_disk = {path.name for path in DEFAULTS.iterdir() if path.is_file()}
        self.assertEqual(set(names), on_disk)
