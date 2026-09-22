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
MODULE_PATH = ROOT / ".github" / "actions" / "resolve-governance" / "resolve.py"
ACTION_PATH = ROOT / ".github" / "actions" / "resolve-governance" / "action.yml"
DEFAULTS_ROOT = ROOT / ".github" / "actions" / "resolve-governance" / "defaults"
IDENTITY_MAPS_ROOT = (
    ROOT / ".github" / "actions" / "resolve-governance" / "identity-maps"
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

    def test_central_semgrep_identity_maps_are_bundled_and_valid(self) -> None:
        self.assertEqual(
            set(module.IDENTITY_MAP_FILENAMES),
            {path.name for path in IDENTITY_MAPS_ROOT.iterdir() if path.is_file()},
        )
        for filename in module.IDENTITY_MAP_FILENAMES:
            with self.subTest(filename=filename):
                module._validate_identity_map(IDENTITY_MAPS_ROOT / filename)

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
                with tempfile.TemporaryDirectory() as temp:
                    with unittest.mock.patch.dict(
                        os.environ,
                        {"GITHUB_WORKSPACE": temp},
                        clear=False,
                    ):
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
                self.assertEqual(
                    ".l9/runtime/org-governance/semgrep-policy.yaml",
                    policy,
                )
                self.assertEqual([], waivers)
                if mode == "disabled":
                    self.assertFalse(required)

    def test_core_defaults_ignore_consumer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": temp},
                clear=False,
            ):
                self.assertEqual(
                    DEFAULTS_ROOT.resolve(),
                    module.core_defaults_path(),
                )

    def test_action_exposes_no_governance_root(self) -> None:
        text = ACTION_PATH.read_text(encoding="utf-8")
        self.assertNotIn("governance-root", text)
        self.assertNotIn("L9_GOVERNANCE_ROOT", text)
        self.assertIn("identity-map-directory:", text)
        self.assertIn("steps.resolve.outputs.identity-map-directory", text)

    def test_defaults_resolve_end_to_end_via_resolver_main(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "L9_PROFILE": "pr_fast",
                "L9_PROVIDER": "semgrep",
                "L9_EVENT_NAME": "pull_request",
                "L9_REPOSITORY": "Quantum-L9/example",
                "L9_REF": "refs/heads/main",
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
        self.assertIn(
            "sdk-policy=.l9/runtime/org-governance/semgrep-policy.yaml",
            output,
        )
        self.assertIn(
            "identity-map-directory=.l9/runtime/org-governance/semgrep-identity-maps",
            output,
        )
        self.assertIn("governance-digest=", output)
        selected_policy = module.select_policy(
            self.documents,
            "pr_fast",
            DEFAULTS_ROOT,
        )
        self.assertEqual(
            64,
            len(module.canonical_digest(DEFAULTS_ROOT, selected_policy)),
        )

    def test_resolve_policy_stages_bundled_file_into_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": str(workspace)},
                clear=False,
            ):
                staged = module.resolve_policy(
                    self.documents,
                    "pr_fast",
                    DEFAULTS_ROOT,
                )
            dest = workspace / staged
            self.assertEqual(
                ".l9/runtime/org-governance/semgrep-policy.yaml",
                staged,
            )
            self.assertTrue(dest.is_file())
            payload = dest.read_text(encoding="utf-8")
            self.assertIn("l9.finding-policy/v1", payload)
            self.assertIn('"mode": "advisory"', payload)

    def test_resolve_policy_validates_only_the_sdk_policy_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            payload = b'{"schema":"l9.finding-policy/v1","opaque":42}'
            policy.write_bytes(payload)
            self.assertEqual(payload, module._validated_sdk_policy_bytes(policy))

    def test_resolve_policy_rejects_invalid_sdk_policy_envelopes(self) -> None:
        cases = {
            "invalid SDK policy JSON": b"not-json",
            "must contain an object": b"[]",
            "unsupported schema": b'{"schema":"l9.finding-policy/v2"}',
        }
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            for message, payload in cases.items():
                with self.subTest(message=message):
                    policy.write_bytes(payload)
                    with self.assertRaisesRegex(module.GovernanceError, message):
                        module._validated_sdk_policy_bytes(policy)

    def test_resolve_policy_rejects_a_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "governance"
            root.mkdir()
            outside = Path(temp) / "outside.json"
            outside.write_text(
                '{"schema":"l9.finding-policy/v1"}',
                encoding="utf-8",
            )
            (root / "policy.json").symlink_to(outside)
            with self.assertRaisesRegex(module.GovernanceError, "source"):
                module._bundled_policy_path(root, "policy.json")

    def test_resolve_policy_rejects_a_symlinked_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            outside = Path(temp) / "outside"
            workspace.mkdir()
            outside.mkdir()
            (workspace / ".l9").symlink_to(outside, target_is_directory=True)
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": str(workspace)},
                clear=False,
            ):
                with self.assertRaisesRegex(module.GovernanceError, "symlink"):
                    module.resolve_policy(
                        self.documents,
                        "pr_fast",
                        DEFAULTS_ROOT,
                    )
            self.assertEqual([], list(outside.iterdir()))

    def test_resolve_policy_rejects_a_symlinked_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            outside = Path(temp) / "outside.json"
            destination = workspace / ".l9" / "runtime" / "org-governance"
            destination.mkdir(parents=True)
            (destination / "semgrep-policy.yaml").symlink_to(outside)
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": str(workspace)},
                clear=False,
            ):
                with self.assertRaisesRegex(module.GovernanceError, "symlink"):
                    module.resolve_policy(
                        self.documents,
                        "pr_fast",
                        DEFAULTS_ROOT,
                    )
            self.assertFalse(outside.exists())

    def test_resolve_policy_replaces_the_target_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            destination = workspace / ".l9" / "runtime" / "org-governance"
            destination.mkdir(parents=True)
            target = destination / "semgrep-policy.yaml"
            target.write_bytes(b"previous policy")
            with (
                unittest.mock.patch.dict(
                    os.environ,
                    {"GITHUB_WORKSPACE": str(workspace)},
                    clear=False,
                ),
                unittest.mock.patch.object(
                    module.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
            ):
                with self.assertRaisesRegex(module.GovernanceError, "replace failed"):
                    module.resolve_policy(
                        self.documents,
                        "pr_fast",
                        DEFAULTS_ROOT,
                    )
            self.assertEqual(b"previous policy", target.read_bytes())
            self.assertEqual(
                [target],
                list(destination.iterdir()),
            )

    def test_identity_maps_stage_inside_the_consumer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": str(workspace)},
                clear=False,
            ):
                staged = module.stage_identity_maps()
            destination = workspace / staged
            self.assertEqual(
                ".l9/runtime/org-governance/semgrep-identity-maps",
                staged,
            )
            self.assertEqual(
                set(module.IDENTITY_MAP_FILENAMES),
                {path.name for path in destination.iterdir() if path.is_file()},
            )
            for filename in module.IDENTITY_MAP_FILENAMES:
                self.assertEqual(
                    (IDENTITY_MAPS_ROOT / filename).read_bytes(),
                    (destination / filename).read_bytes(),
                )

    def test_identity_maps_reject_a_symlinked_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            outside = Path(temp) / "outside"
            workspace.mkdir()
            outside.mkdir()
            destination_parent = workspace / ".l9" / "runtime" / "org-governance"
            destination_parent.mkdir(parents=True)
            (destination_parent / "semgrep-identity-maps").symlink_to(
                outside,
                target_is_directory=True,
            )
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": str(workspace)},
                clear=False,
            ):
                with self.assertRaises(module.GovernanceError):
                    module.stage_identity_maps()
            self.assertEqual([], list(outside.iterdir()))

    def test_identity_maps_reject_a_symlinked_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            outside = Path(temp) / "outside.yaml"
            workspace.mkdir()
            destination = (
                workspace
                / ".l9"
                / "runtime"
                / "org-governance"
                / "semgrep-identity-maps"
            )
            destination.mkdir(parents=True)
            (destination / "python.yaml").symlink_to(outside)
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": str(workspace)},
                clear=False,
            ):
                with self.assertRaises(module.GovernanceError):
                    module.stage_identity_maps()
            self.assertFalse(outside.exists())

    def test_governance_digest_binds_the_bundled_identity_map_bytes(self) -> None:
        selected_policy = module.select_policy(
            self.documents,
            "pr_fast",
            DEFAULTS_ROOT,
        )
        baseline = module.canonical_digest(DEFAULTS_ROOT, selected_policy)
        with tempfile.TemporaryDirectory() as temp:
            maps_root = Path(temp)
            for filename in module.IDENTITY_MAP_FILENAMES:
                source = IDENTITY_MAPS_ROOT / filename
                (maps_root / filename).write_bytes(source.read_bytes())
            modified = maps_root / module.IDENTITY_MAP_FILENAMES[0]
            modified.write_bytes(modified.read_bytes() + b"\n")
            self.assertNotEqual(
                baseline,
                module.canonical_digest(
                    DEFAULTS_ROOT,
                    selected_policy,
                    maps_root,
                ),
            )

    def test_governance_digest_binds_selected_policy_filename_and_raw_bytes(
        self,
    ) -> None:
        selected_policy = module.select_policy(
            self.documents,
            "pr_fast",
            DEFAULTS_ROOT,
        )
        assert selected_policy is not None
        filename, payload = selected_policy
        baseline = module.canonical_digest(DEFAULTS_ROOT, selected_policy)
        self.assertNotEqual(
            baseline,
            module.canonical_digest(DEFAULTS_ROOT, (f"copy-{filename}", payload)),
        )
        self.assertNotEqual(
            baseline,
            module.canonical_digest(DEFAULTS_ROOT, (filename, payload + b"\n")),
        )


if __name__ == "__main__":
    unittest.main()
