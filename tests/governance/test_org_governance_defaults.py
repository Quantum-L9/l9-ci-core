"""Central Core governance defaults bundled with resolve-governance."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
)
ACTION_PATH = (
    ROOT / ".github" / "actions" / "resolve-governance" / "action.yml"
)
DEFAULTS_ROOT = (
    ROOT / ".github" / "actions" / "resolve-governance" / "defaults"
)
CONTRACT_PATH = ROOT / ".l9" / "org-runtime-contract.yaml"

spec = importlib.util.spec_from_file_location("resolve_governance", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

KNOWN_GOVERNANCE_FILES = set(module.EXPECTED_SCHEMAS)


def contract_event_classes() -> list[str]:
    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    return list(contract["inputs"]["event"]["enum"])


class OrgGovernanceDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = module.load_documents(DEFAULTS_ROOT)

    def test_defaults_are_exactly_the_six_known_files(self) -> None:
        entries = {path.name for path in DEFAULTS_ROOT.iterdir() if path.is_file()}
        self.assertEqual(KNOWN_GOVERNANCE_FILES, entries)

    def test_every_default_document_loads_with_canonical_schema(self) -> None:
        self.assertEqual(KNOWN_GOVERNANCE_FILES, set(self.documents))

    def test_contract_event_classes_are_all_covered(self) -> None:
        profiles = self.documents["execution-profiles.yaml"]["profiles"]
        covered: set[str] = set()
        for profile in profiles.values():
            covered.update(profile.get("allowed_events", []))
        self.assertEqual(set(contract_event_classes()), covered, covered)

    def test_every_standard_profile_resolves(self) -> None:
        profiles = self.documents["execution-profiles.yaml"]["profiles"]
        for profile_name, profile in profiles.items():
            with self.subTest(profile=profile_name):
                event_name = profile["allowed_events"][0]
                validated = module.validate_profile(
                    self.documents,
                    profile_name,
                    "semgrep",
                    event_name,
                )
                mode = module.resolve_mode(
                    self.documents,
                    profile_name,
                    "semgrep",
                    validated["default_mode"],
                )
                required = module.resolve_requiredness(
                    self.documents,
                    profile_name,
                    "semgrep",
                )
                policy = module.resolve_policy(
                    self.documents,
                    profile_name,
                    DEFAULTS_ROOT,
                )
                waivers = module.applicable_waivers(
                    self.documents,
                    profile=profile_name,
                    provider="semgrep",
                    repository="Quantum-L9/example",
                    ref="refs/heads/main",
                    today=module.dt.date(2026, 8, 21),
                )
                self.assertIn(
                    validated["sdk_profile"],
                    module.ALLOWED_SDK_PROFILES,
                )
                self.assertIn(mode, module.ALLOWED_MODES)
                self.assertEqual("", policy)
                self.assertEqual([], waivers)
                if mode == "disabled":
                    self.assertFalse(required)

    def test_core_defaults_token_ignores_consumer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": temp},
                clear=False,
            ):
                self.assertEqual(
                    DEFAULTS_ROOT.resolve(),
                    module.governance_path(module.CORE_DEFAULTS_TOKEN),
                )

    def test_action_defaults_to_core_bundle(self) -> None:
        text = ACTION_PATH.read_text(encoding="utf-8")
        self.assertIn('default: "@core-defaults"', text)
        self.assertNotIn("default: .github/governance", text)

    def test_defaults_resolve_end_to_end_via_resolver_main(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "L9_PROFILE": "pr_fast",
                "L9_PROVIDER": "semgrep",
                "L9_EVENT_NAME": "pull_request",
                "L9_REPOSITORY": "Quantum-L9/example",
                "L9_REF": "refs/heads/main",
                "L9_GOVERNANCE_ROOT": module.CORE_DEFAULTS_TOKEN,
                "GITHUB_WORKSPACE": temp,
            }
            captured = io.StringIO()
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("GITHUB_OUTPUT", None)
                with contextlib.redirect_stdout(captured):
                    exit_code = module.main()
        output = captured.getvalue()
        self.assertEqual(0, exit_code, output)
        self.assertIn("enabled=true", output)
        self.assertIn("mode=blocking", output)
        self.assertIn("governance-digest=", output)
        self.assertEqual(64, len(module.canonical_digest(DEFAULTS_ROOT)))

    def test_workspace_override_cannot_escape_consumer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": temp},
                clear=False,
            ):
                with self.assertRaises(module.GovernanceError):
                    module.governance_path("../outside")


if __name__ == "__main__":
    unittest.main()
