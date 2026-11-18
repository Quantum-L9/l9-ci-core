"""Core's own SonarQube Cloud project is analysed, and the scan is not a gate.

The project Quantum-L9_l9-ci-core existed with no analysis. The scan uses the
organization SONAR_TOKEN. It stays off the required-check set, does not wait
on the quality gate, and does not run for a fork pull request.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "self-sonar.yml"
PROPERTIES = ROOT / "sonar-project.properties"
RULESET = ROOT / "docs" / "release" / "rulesets" / "core-main-protection.json"

SCAN_ACTION = (
    "SonarSource/sonarqube-scan-action@59202af334b4ba7b5db01b701b8e79f8b3bef0f4"
)
CHECKOUT_ACTION = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
SCANNER_VERSION = "8.4.0.16527"


class SelfSonarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_scan_action_and_checkout_are_full_shas(self) -> None:
        self.assertIn(f"uses: {SCAN_ACTION} # v8.5.0", self.text)
        self.assertIn(f"uses: {CHECKOUT_ACTION} # v4.2.0", self.text)
        self.assertIn(f"scannerVersion: {SCANNER_VERSION}", self.text)
        for reference in re.findall(r"uses:\s*(\S+)", self.text):
            self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")

    def test_token_is_the_named_organization_secret(self) -> None:
        self.assertIn("SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}", self.text)
        self.assertNotIn("secrets: inherit", self.text)

    def test_scan_is_not_a_quality_gate_and_skips_forks(self) -> None:
        properties = PROPERTIES.read_text(encoding="utf-8")
        self.assertNotIn("qualitygate.wait", self.text)
        self.assertNotIn("sonar.qualitygate.wait", properties)
        self.assertIn("github.event_name != 'pull_request'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )

    def test_permissions_are_contents_read(self) -> None:
        self.assertRegex(self.text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))
        self.assertIsNone(re.search(r":\s+write\s*$", self.text, re.MULTILINE))

    def test_properties_name_the_existing_project(self) -> None:
        properties = PROPERTIES.read_text(encoding="utf-8")
        self.assertIn("sonar.organization=quantum-l9", properties)
        self.assertIn("sonar.projectKey=Quantum-L9_l9-ci-core", properties)
        self.assertIn("sonar.python.version=3.12", properties)

    def test_ruleset_does_not_require_the_sonar_check(self) -> None:
        document = json.loads(RULESET.read_text(encoding="utf-8"))
        contexts = [
            check["context"]
            for rule in document["rules"]
            if rule["type"] == "required_status_checks"
            for check in rule["parameters"]["required_status_checks"]
        ]
        self.assertTrue(contexts)
        self.assertFalse(any("sonar" in context.lower() for context in contexts))


if __name__ == "__main__":
    unittest.main()
