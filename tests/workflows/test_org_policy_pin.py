"""The required Core job fails closed on the unsigned organization-policy pin."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
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


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def policy_step(text: str) -> str:
    marker = "      - id: org-policy\n"
    start = text.index(marker)
    return text[start:]


def policy_script(text: str) -> str:
    step = policy_step(text)
    heredoc = step.split("python3 - <<'PY'\n", 1)[1]
    body = heredoc.split("\n          PY", 1)[0]
    return textwrap.dedent(body)


def run_policy(script: str, **subject: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
    if subject:
        env["L9_ORG_POLICY_SUBJECT_FIXTURE"] = "1"
        env.update(subject)
    return subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


class OrgPolicyPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = workflow_text()
        self.step = policy_step(self.text)
        self.script = policy_script(self.text)

    def test_step_is_unconditional_on_the_required_job(self) -> None:
        self.assertLess(
            self.text.index("name: Enforce central mode on SDK technical gate"),
            self.text.index("      - id: org-policy"),
        )
        self.assertIn("name: Analyze (central Core)", self.text)
        header, _, _ = self.step.partition("run: |")
        self.assertNotIn("continue-on-error", header)
        self.assertNotIn("if:", header)
        self.assertNotIn("always()", self.step)
        self.assertNotIn(".l9/ci.json", self.script)
        self.assertNotIn("open(", self.script)

    def test_defaults_registry_stays_six_files(self) -> None:
        source = RESOLVE.read_text(encoding="utf-8")
        block = re.search(r"EXPECTED_SCHEMAS = \{([^}]+)\}", source)
        self.assertIsNotNone(block)
        assert block is not None
        names = re.findall(r'"([^"]+)"\s*:', block.group(1))
        self.assertEqual(6, len(names))
        on_disk = {path.name for path in DEFAULTS.iterdir() if path.is_file()}
        self.assertEqual(set(names), on_disk)

    def test_pinned_candidate_is_critical_unknown(self) -> None:
        result = run_policy(self.script)
        self.assertEqual(1, result.returncode)
        self.assertIn("org-policy-admission: false", result.stdout)
        self.assertIn("org-policy-signed-release: false", result.stdout)
        self.assertIn("org-policy-signer: UNKNOWN", result.stdout)
        self.assertIn("org-policy-verdict: critical-unknown", result.stdout)
        for key, value in PIN.items():
            self.assertIn(value, result.stdout)

    def test_named_signer_is_not_a_pass(self) -> None:
        result = run_policy(self.script, SUBJECT_SIGNER="governance-admins")
        self.assertEqual(1, result.returncode)
        self.assertIn("org-policy-verdict: critical-unknown", result.stdout)
        self.assertIn("org-policy-unverified-signer: governance-admins", result.stderr)
        self.assertNotIn("org-policy-verdict: pass", result.stdout)

    def test_tampered_subject_fails(self) -> None:
        cases = {
            "SUBJECT_HEAD": "0" * 40,
            "SUBJECT_VERSION": "4.0.0",
            "SUBJECT_REPOSITORY": "Quantum-L9/Cursor-Governance",
            "SUBJECT_PATH": "ORG_INVARIANTS.yaml",
            "SUBJECT_PULL_REQUEST": "1",
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                result = run_policy(self.script, **{name: value})
                self.assertEqual(1, result.returncode)
                self.assertIn("org-policy-verdict: fail", result.stdout)
                self.assertNotIn("org-policy-verdict: critical-unknown", result.stdout)
                self.assertNotIn("org-policy-verdict: pass", result.stdout)
