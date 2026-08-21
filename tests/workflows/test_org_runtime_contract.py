"""Central organization runtime contract consistency."""

from __future__ import annotations

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / ".l9" / "org-runtime-contract.yaml"
ENTRYPOINT_PATH = ROOT / ".github" / "workflows" / "org-ci.yml"
CONSUMER_SCHEMA_PATH = ROOT / ".l9" / "ci-consumer.schema.json"
SHA_RE = re.compile(r"@[0-9a-f]{40}\b")


def load_contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


class OrgRuntimeContractTests(unittest.TestCase):
    def test_contract_declares_central_ruleset_entrypoint(self) -> None:
        contract = load_contract()
        self.assertEqual("l9.org-runtime-contract/v1", contract["schema"])
        self.assertEqual(".github/workflows/org-ci.yml", contract["entrypoint"]["workflow"])
        self.assertEqual(
            "github_organization_required_workflow_ruleset",
            contract["entrypoint"]["enforcement_mechanism"],
        )
        self.assertFalse(contract["entrypoint"]["consumer_copy_required"])
        self.assertFalse(contract["entrypoint"]["consumer_core_pin_allowed"])

    def test_entrypoint_supports_ruleset_and_canary_events(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        header = text.split("permissions:", 1)[0]
        for trigger in ("pull_request:", "merge_group:", "workflow_dispatch:", "workflow_call:"):
            self.assertIn(trigger, header)
        self.assertNotIn("schedule:", header)
        self.assertNotRegex(header, r"(?m)^\s{2}push:\s*$")

    def test_entrypoint_does_not_accept_consumer_governance_or_language_authority(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        header = text.split("permissions:", 1)[0]
        self.assertNotRegex(header, r"(?m)^\s{6}governance:\s*$")
        self.assertNotRegex(header, r"(?m)^\s{6}language:\s*$")
        self.assertNotIn(".github/governance", text)
        self.assertNotIn(".github/org-governance-defaults", text)
        self.assertIn('governance-root: "@core-defaults"', text)

    def test_entrypoint_composes_only_full_sha_core_primitives(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        actions = (
            "resolve-consumer-metadata",
            "resolve-governance",
            "provision-sdk",
            "invoke-sdk",
            "validate-bundle",
            "route-artifacts",
            "build-artifact-manifest",
        )
        pins: set[str] = set()
        for action in actions:
            match = re.search(
                rf"Quantum-L9/l9-ci-core/\.github/actions/{re.escape(action)}@([0-9a-f]{{40}})",
                text,
            )
            self.assertIsNotNone(match, action)
            assert match is not None
            pins.add(match.group(1))
        self.assertEqual(1, len(pins), pins)
        publish = re.search(
            r"Quantum-L9/l9-ci-core/\.github/workflows/publish-analysis\.yml@([0-9a-f]{40})",
            text,
        )
        self.assertIsNotNone(publish)
        assert publish is not None
        self.assertEqual(next(iter(pins)), publish.group(1))

    def test_sdk_owns_capability_detection(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        self.assertIn("providers detect --root . --format json", text)
        self.assertIn("consumer repo_class=python conflicts with SDK capability detection", text)
        self.assertIn("consumer repo_class=typescript conflicts with SDK capability detection", text)
        self.assertIn("SDK capability detection is ambiguous", text)

    def test_failure_publication_is_not_skipped(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        self.assertIn("if: always() && needs.analyze.outputs.enabled == 'true'", text)
        self.assertIn("workflow-result: ${{ needs.analyze.result }}", text)

    def test_consumer_schema_is_descriptive_only(self) -> None:
        import json

        schema = json.loads(CONSUMER_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema", "owner", "repo_class", "waiver_refs"},
            set(schema["properties"]),
        )
        self.assertFalse(schema["additionalProperties"])

    def test_contract_explicitly_prohibits_distribution_architecture(self) -> None:
        contract = load_contract()
        prohibited = set(contract["ownership"]["prohibited"])
        self.assertIn("CI distribution from Quantum-L9/.github", prohibited)
        self.assertIn("copied L9 workflows in consumer repositories as an enforcement mechanism", prohibited)
        self.assertIn("copied L9 governance packs in consumer repositories", prohibited)
        self.assertIn("a second organization CI control-plane repository", prohibited)

    def test_internal_and_sdk_pinning_fail_closed(self) -> None:
        contract = load_contract()
        self.assertEqual("full-40-char-sha", contract["pinning"]["core_internal_actions"]["policy"])
        self.assertFalse(contract["pinning"]["core_internal_actions"]["floating_references_allowed"])
        sdk = contract["pinning"]["sdk_revision"]
        self.assertFalse(sdk["floating_git_references_allowed"])
        self.assertFalse(sdk["branches_allowed"])
        self.assertFalse(sdk["tags_allowed"])
        self.assertFalse(sdk["short_git_revisions_allowed"])


if __name__ == "__main__":
    unittest.main()
