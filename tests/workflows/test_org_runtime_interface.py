"""Interface manifest validator (l9.org-runtime-interface/v1).

The machine-readable candidate interface (.l9/org-runtime-interface.yaml)
declares claims about the executable organization entrypoint. This validator
does not trust the manifest: every claim marked VALIDATED is re-derived
against executable Core behavior (workflow text, action pins, default pack,
SDK compatibility contract). Claims that cannot be re-derived must stay
UNKNOWN — asserting that is part of the contract.
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

INTERFACE_PATH = ROOT / ".l9" / "org-runtime-interface.yaml"
CONTRACT_PATH = ROOT / ".l9" / "org-runtime-contract.yaml"
ENTRYPOINT_PATH = ROOT / ".github" / "workflows" / "org-ci.yml"
DEFAULTS_ROOT = ROOT / ".github" / "org-governance-defaults"
SDK_COMPAT_PATH = ROOT / ".l9" / "sdk-compatibility.yaml"

# The known governance filenames are owned by the resolve-governance action
# (EXPECTED_SCHEMAS) — this validator derives from that single source of
# truth instead of maintaining a second copy.
RESOLVE_PATH = ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
spec = importlib.util.spec_from_file_location("resolve_governance", RESOLVE_PATH)
assert spec and spec.loader
resolve_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolve_module)

KNOWN_GOVERNANCE_FILES = set(resolve_module.EXPECTED_SCHEMAS)
COMPOSED_ACTIONS = (
    "resolve-governance",
    "provision-sdk",
    "invoke-sdk",
    "validate-bundle",
    "route-artifacts",
    "build-artifact-manifest",
)
SHA_RE = re.compile(r"[0-9a-f]{40}")


def load_interface() -> dict:
    if not INTERFACE_PATH.is_file():
        raise FileNotFoundError(INTERFACE_PATH)
    return yaml.safe_load(INTERFACE_PATH.read_text(encoding="utf-8"))


def workflow_text() -> str:
    return ENTRYPOINT_PATH.read_text(encoding="utf-8")


class OrgRuntimeInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = load_interface()
        self.claims = {claim["id"]: claim for claim in self.interface["claims"]}

    def test_manifest_shape_and_claim_statuses(self) -> None:
        self.assertEqual(self.interface["schema"], "l9.org-runtime-interface/v1")
        self.assertEqual(self.interface["metadata"]["status"], "candidate")
        contract = self.interface["metadata"]["companion_contract"]
        self.assertTrue((ROOT / contract).is_file(), contract)
        for claim in self.interface["claims"]:
            with self.subTest(claim=claim.get("id")):
                self.assertIn(claim["status"], {"VALIDATED", "UNKNOWN"})
                self.assertTrue(claim.get("statement"))
                for evidence in claim.get("evidence", []):
                    self.assertTrue(
                        (ROOT / evidence).exists(),
                        f"claim {claim['id']} evidence missing: {evidence}",
                    )

    def test_unknown_claims_stay_unknown(self) -> None:
        # The validator never fabricates: claims without executable evidence
        # must be marked UNKNOWN in the manifest.
        for claim_id in ("control-plane-live-consumption", "remote-end-to-end-run"):
            self.assertEqual(
                "UNKNOWN",
                self.claims[claim_id]["status"],
                claim_id,
            )
            self.assertEqual([], self.claims[claim_id]["evidence"])

    def test_entrypoint_reusable_claim_matches_execution(self) -> None:
        text = workflow_text()
        self.assertIn("workflow_call:", text)
        self.assertNotIn("push:", text.split("workflow_call:")[0])
        self.assertNotIn("pull_request:", text.split("workflow_call:")[0])
        self.assertNotIn("schedule:", text.split("workflow_call:")[0])
        for action in COMPOSED_ACTIONS:
            with self.subTest(action=action):
                match = SHA_RE.search(text)
                self.assertIsNotNone(match)
                pinned = f"/.github/actions/{action}@{match.group(0)}"
                self.assertIn(pinned, text, action)

    def test_governance_bounded_claim_matches_execution(self) -> None:
        text = workflow_text()
        for name in KNOWN_GOVERNANCE_FILES:
            self.assertIn(f'"{name}"', text, name)
        self.assertIn("unknown = sorted(set(pack) - KNOWN)", text)
        self.assertIn('sys.exit(f"governance input is not valid JSON: {exc}")', text)
        self.assertIn('sys.exit(f"unsafe governance filename: {name}")', text)

    def test_core_defaults_claim_matches_execution(self) -> None:
        entries = {path.name for path in DEFAULTS_ROOT.iterdir() if path.is_file()}
        self.assertEqual(KNOWN_GOVERNANCE_FILES, entries)
        text = workflow_text()
        self.assertIn(
            'defaults = pathlib.Path(".github/org-governance-defaults")',
            text,
        )
        self.assertIn("Core standard governance defaults missing", text)

    def test_sdk_revision_claim_matches_execution(self) -> None:
        compatibility = yaml.safe_load(SDK_COMPAT_PATH.read_text(encoding="utf-8"))
        default = compatibility["default"]["revision"]
        self.assertRegex(default, SHA_RE)
        supported = {entry["revision"] for entry in compatibility["supported"]}
        self.assertIn(default, supported)
        policy = compatibility["policy"]
        for key in (
            "floating_git_references_allowed",
            "branches_allowed",
            "tags_allowed",
            "short_git_revisions_allowed",
            "unlisted_revisions_allowed",
        ):
            self.assertFalse(policy[key], key)

    def test_publication_outputs_claim_matches_execution(self) -> None:
        text = workflow_text()
        for output in ("artifact-name", "gate-status", "sdk-revision"):
            with self.subTest(output=output):
                self.assertIn(f"jobs.analyze.outputs.{output}", text, output)
        self.assertIn("uses: ./.github/workflows/publish-analysis.yml", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("Blocking mode: failing job on SDK gate verdict", text)

    def test_contract_references_this_validator(self) -> None:
        contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        tests = contract["validation"]["contract_tests"]
        self.assertIn("tests/workflows/test_org_runtime_interface.py", tests)
        admission = contract["validation"]["admission"]
        self.assertIn(".l9/org-runtime-interface.yaml", admission)


if __name__ == "__main__":
    unittest.main()
