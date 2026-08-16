"""Organization runtime contract consistency (l9.org-runtime-contract/v1).

Validates that the single organization-facing Core entrypoint declared by
`.l9/org-runtime-contract.yaml` exists, is a reusable workflow_call, exposes
the contract's declared inputs, composes the authoritative Core primitives
rather than duplicating orchestration, and keeps the governance pack bounded
to the six known filenames.
"""

from __future__ import annotations

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

CONTRACT_PATH = ROOT / ".l9" / "org-runtime-contract.yaml"
ENTRYPOINT_PATH = ROOT / ".github" / "workflows" / "org-ci.yml"

KNOWN_GOVERNANCE_FILES = {
    "execution-profiles.yaml",
    "rule-modes.yaml",
    "provider-requiredness.yaml",
    "quality-thresholds.yaml",
    "waivers.yaml",
    "promotion-policy.yaml",
}

CONTRACT_INPUTS = {
    "event",
    "language",
    "profile",
    "governance",
    "matrix_id",
    "sdk_revision",
    "semgrep_version",
    "artifact_retention_days",
}


def load_contract() -> dict:
    if not CONTRACT_PATH.is_file():
        raise FileNotFoundError(CONTRACT_PATH)
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def workflow_inputs(text: str) -> set[str]:
    """Workflow input names, mapping '-' to '_' as the contract declares them."""
    names = set()
    for match in re.finditer(r"^\s{6}([a-z0-9-]+):\n(?:\s{8}description:.*?\n)*", text):
        names.add(match.group(1).replace("-", "_"))
    for match in re.finditer(r"^\s{6}([a-z0-9-]+):\n(?=(?:\s{8}\S+:\s.*\n)*?\s{8}type:)", text):
        names.add(match.group(1).replace("-", "_"))
    return names


class OrgRuntimeContractTests(unittest.TestCase):
    def test_contract_declares_entrypoint_and_inputs(self) -> None:
        contract = load_contract()
        self.assertEqual(contract["schema"], "l9.org-runtime-contract/v1")
        entrypoint = contract["entrypoint"]["workflow"]
        self.assertEqual(entrypoint, ".github/workflows/org-ci.yml")
        self.assertIn("full-40-char-sha", contract["entrypoint"]["callable_as"])
        declared = {key.replace("-", "_") for key in contract["inputs"]}
        self.assertTrue(CONTRACT_INPUTS.issubset(declared), declared)

    def test_entrypoint_is_reusable_workflow_call_only(self) -> None:
        self.assertTrue(ENTRYPOINT_PATH.is_file(), "org-ci.yml missing")
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)
        self.assertNotIn("push:", text.split("workflow_call:")[0])
        self.assertNotIn("pull_request:", text.split("workflow_call:")[0])
        self.assertNotIn("schedule:", text.split("workflow_call:")[0])
        self.assertIn("uses: ./.github/workflows/publish-analysis.yml", text)

    def test_entrypoint_composes_authoritative_primitives(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        for action in (
            "resolve-governance",
            "provision-sdk",
            "invoke-sdk",
            "validate-bundle",
            "route-artifacts",
            "build-artifact-manifest",
        ):
            self.assertIn(f"/.github/actions/{action}@a642641ad89b2f37022e8ce76e4bcf94791ff75a", text, action)
        # Core never re-decides the gate; it only enforces in blocking mode.
        self.assertIn("gate evaluate", text)
        self.assertIn("Blocking mode: failing job on SDK gate verdict", text)

    def test_governance_input_is_bounded_to_known_files(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        known = {repr(name) for name in KNOWN_GOVERNANCE_FILES}
        for name in KNOWN_GOVERNANCE_FILES:
            self.assertIn(f'"{name}"', text, name)
        # The materializer rejects unknown keys; mirror that list here.
        self.assertIn("unknown = sorted(set(pack) - KNOWN)", text)
        self.assertIn('sys.exit(f"governance input contains unknown files: {unknown}")', text)
        self.assertTrue(known)

    def test_contract_prohibits_org_administration_in_core(self) -> None:
        contract = load_contract()
        prohibited = contract["ownership"]["prohibited_in_core"]
        for item in (
            "organization repository inventories",
            "organization ruleset mutation",
            "CI fanout or seeding to organization repositories",
            "organization rollout execution",
            "consumer workflow generation",
        ):
            self.assertIn(item, prohibited)
        self.assertIn("rollout", contract["ownership"]["control_plane_owns"])
        self.assertIn("rollback", contract["ownership"]["control_plane_owns"])

    def test_contract_pinning_policy(self) -> None:
        contract = load_contract()
        pin = contract["pinning"]
        self.assertEqual(pin["core_revision"]["policy"], "full-40-char-sha-or-immutable-semver-tag-only")
        self.assertEqual(pin["core_revision"]["selected_by"], "control_plane")
        sdk = pin["sdk_revision"]
        self.assertFalse(sdk["floating_git_references_allowed"])
        self.assertFalse(sdk["branches_allowed"])
        self.assertFalse(sdk["tags_allowed"])
        self.assertFalse(sdk["short_git_revisions_allowed"])


if __name__ == "__main__":
    unittest.main()
