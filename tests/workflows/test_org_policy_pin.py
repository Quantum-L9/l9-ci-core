"""Organization-policy admission stays off the required job until a signer is admitted.

The open ``Quantum-L9/.github`` pull request 269 candidate is not an admitted
policy. The required ``Analyze (central Core)`` job therefore does not run an
organization-policy step. ``admit`` is the decision the job may call once an
admitted signer exists: a matching subject and an admitted signer pass, and
every other subject or signer fails closed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "org-ci.yml"
RESOLVE = ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
DEFAULTS = ROOT / ".github" / "actions" / "resolve-governance" / "defaults"

PIN = {
    "repository": "Quantum-L9/.github",
    "pull_request": "269",
    "head": "ec3f5c6734530a3a90438a80827ebd8edd876734",
    "path": "policies/ORG_INVARIANTS.yaml",
    "version": "4.1.0",
}

# Empty until Cursor-Governance records an admitted signer for PIN.
ADMITTED_SIGNERS: frozenset[str] = frozenset()


def admit(
    subject: dict[str, str],
    signer: str,
    admitted_signers: frozenset[str],
) -> tuple[str, int]:
    """Return ``(verdict, exit_code)`` for one policy subject."""
    if subject != PIN:
        return "fail", 1
    if not signer or signer == "UNKNOWN" or signer not in admitted_signers:
        return "critical-unknown", 1
    return "pass", 0


class OrgPolicyPinTests(unittest.TestCase):
    def test_required_job_has_no_organization_policy_step(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: Analyze (central Core)", text)
        self.assertNotIn("id: org-policy", text)
        self.assertNotIn("org-policy-verdict", text)
        self.assertNotIn("continue-on-error", text)

    def test_defaults_registry_stays_six_files(self) -> None:
        source = RESOLVE.read_text(encoding="utf-8")
        block = re.search(r"EXPECTED_SCHEMAS = \{([^}]+)\}", source)
        self.assertIsNotNone(block)
        assert block is not None
        names = re.findall(r'"([^"]+)"\s*:', block.group(1))
        self.assertEqual(6, len(names))
        on_disk = {path.name for path in DEFAULTS.iterdir() if path.is_file()}
        self.assertEqual(set(names), on_disk)

    def test_live_pin_is_not_admitted(self) -> None:
        self.assertEqual(frozenset(), ADMITTED_SIGNERS)
        self.assertEqual(("critical-unknown", 1), admit(PIN, "UNKNOWN", ADMITTED_SIGNERS))
        self.assertEqual(
            ("critical-unknown", 1),
            admit(PIN, "governance-admins", ADMITTED_SIGNERS),
        )

    def test_admitted_signer_passes(self) -> None:
        admitted = frozenset({"fixture-admitted"})
        self.assertEqual(("pass", 0), admit(dict(PIN), "fixture-admitted", admitted))

    def test_tampered_subject_fails_even_with_an_admitted_signer(self) -> None:
        admitted = frozenset({"fixture-admitted"})
        replacements = {
            "head": "0" * 40,
            "version": "4.0.0",
            "repository": "Quantum-L9/Cursor-Governance",
            "path": "ORG_INVARIANTS.yaml",
            "pull_request": "1",
        }
        for key, value in replacements.items():
            with self.subTest(key=key):
                subject = dict(PIN)
                subject[key] = value
                self.assertEqual(("fail", 1), admit(subject, "fixture-admitted", admitted))
