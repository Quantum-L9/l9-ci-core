"""Interface manifest validator for the central Core runtime."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
INTERFACE_PATH = ROOT / ".l9" / "org-runtime-interface.yaml"
CONTRACT_PATH = ROOT / ".l9" / "org-runtime-contract.yaml"
ENTRYPOINT_PATH = ROOT / ".github" / "workflows" / "org-ci.yml"
DEFAULTS_ROOT = ROOT / ".github" / "actions" / "resolve-governance" / "defaults"
CONSUMER_SCHEMA_PATH = ROOT / ".l9" / "ci-consumer.schema.json"
SDK_COMPAT_PATH = ROOT / ".l9" / "sdk-compatibility.yaml"
SHA_RE = re.compile(r"[0-9a-f]{40}")


def load_interface() -> dict:
    return yaml.safe_load(INTERFACE_PATH.read_text(encoding="utf-8"))


def workflow_text() -> str:
    return ENTRYPOINT_PATH.read_text(encoding="utf-8")


class OrgRuntimeInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = load_interface()
        self.claims = {claim["id"]: claim for claim in self.interface["claims"]}

    def test_manifest_shape_and_claim_statuses(self) -> None:
        self.assertEqual("l9.org-runtime-interface/v1", self.interface["schema"])
        self.assertEqual("candidate", self.interface["metadata"]["status"])
        for claim in self.interface["claims"]:
            with self.subTest(claim=claim.get("id")):
                self.assertIn(claim["status"], {"VALIDATED", "UNKNOWN"})
                self.assertTrue(claim.get("statement"))
                for evidence in claim.get("evidence", []):
                    self.assertTrue(
                        (ROOT / evidence).exists(), f"missing evidence: {evidence}"
                    )

    def test_external_runtime_claims_stay_unknown(self) -> None:
        for claim_id in (
            "organization-ruleset-live-enforcement",
            "remote-end-to-end-run",
        ):
            self.assertEqual("UNKNOWN", self.claims[claim_id]["status"])
            self.assertEqual([], self.claims[claim_id]["evidence"])

    def test_ruleset_trigger_claim_matches_workflow(self) -> None:
        text = workflow_text().split("permissions:", 1)[0]
        self.assertIn("pull_request:", text)
        self.assertIn("merge_group:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("workflow_call:", text)

    def test_central_defaults_claim_matches_execution(self) -> None:
        expected = {
            "execution-profiles.yaml",
            "rule-modes.yaml",
            "provider-requiredness.yaml",
            "quality-thresholds.yaml",
            "waivers.yaml",
            "promotion-policy.yaml",
        }
        entries = {path.name for path in DEFAULTS_ROOT.iterdir() if path.is_file()}
        self.assertEqual(expected, entries)
        text = workflow_text()
        self.assertIn('governance-root: "@core-defaults"', text)
        self.assertNotIn(".github/org-governance-defaults", text)

    def test_consumer_metadata_claim_matches_schema_and_workflow(self) -> None:
        schema = json.loads(CONSUMER_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema", "owner", "repo_class", "waiver_refs"},
            set(schema["properties"]),
        )
        self.assertFalse(schema["additionalProperties"])
        text = workflow_text()
        self.assertIn("resolve-consumer-metadata", text)
        self.assertIn("consumer waiver_refs are not centrally applicable", text)

    def test_sdk_capability_claim_matches_execution(self) -> None:
        text = workflow_text()
        self.assertIn("providers detect --root . --format json", text)
        self.assertIn("SDK capability detection is ambiguous", text)

    def test_sdk_revision_claim_matches_execution(self) -> None:
        compatibility = yaml.safe_load(SDK_COMPAT_PATH.read_text(encoding="utf-8"))
        default = compatibility["default"]["revision"]
        self.assertRegex(default, SHA_RE)
        supported = {entry["revision"] for entry in compatibility["supported"]}
        self.assertIn(default, supported)
        for key in (
            "floating_git_references_allowed",
            "branches_allowed",
            "tags_allowed",
            "short_git_revisions_allowed",
            "unlisted_revisions_allowed",
        ):
            self.assertFalse(compatibility["policy"][key], key)

    def test_required_workflow_read_only_claim_matches_execution(self) -> None:
        text = workflow_text()
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        self.assertEqual([], write_pattern.findall(text))
        self.assertIn("contents: read", text)

    def test_blocking_result_visibility_claim_matches_execution(self) -> None:
        text = workflow_text()
        summary = text.index("name: Write central CI summary")
        enforce = text.index("name: Enforce central mode on SDK technical gate")
        self.assertLess(summary, enforce)
        self.assertIn("if: always()", text[summary:enforce])

    def test_no_distribution_claim_matches_contract(self) -> None:
        contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        prohibited = set(contract["ownership"]["prohibited"])
        self.assertIn("CI distribution from Quantum-L9/.github", prohibited)
        self.assertFalse(contract["entrypoint"]["consumer_copy_required"])

    def test_contract_references_this_validator(self) -> None:
        contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertIn(
            "tests/workflows/test_org_runtime_interface.py",
            contract["validation"]["contract_tests"],
        )
        self.assertIn("UNKNOWN", contract["validation"]["admission"])


if __name__ == "__main__":
    unittest.main()
