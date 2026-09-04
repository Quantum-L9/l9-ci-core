"""Behavioral tests for ``.github/actions/validate-release/validate_release.py``.

The validator runs in a bare release checkout (no PyYAML), reads the expected
version from ``.l9/repo-spec.yaml``, and fails closed on a moving alias, a
version mismatch, or a missing contract. The full ``unittest`` run and the
external-pin scan are exercised elsewhere; here they are stubbed so each
assertion isolates one rule.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / ".github" / "actions" / "validate-release" / "validate_release.py"
CONTRACTS = (
    ".l9/repo-spec.yaml",
    ".l9/architecture.yaml",
    ".l9/publication-contract.yaml",
    ".l9/release-plane.yaml",
)


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_release", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ValidateReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_validator()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for relative in CONTRACTS:
            target = self.tmp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / relative, target)
        self.declared = self.module.declared_version(ROOT)

    def run_main(self, tag: str, expected: str = "") -> int:
        env = {
            "GITHUB_WORKSPACE": str(self.tmp),
            "L9_RELEASE_TAG": tag,
            "L9_EXPECTED_VERSION": expected,
        }
        with (
            unittest.mock.patch.dict(os.environ, env, clear=False),
            unittest.mock.patch.object(self.module, "run_tests"),
            unittest.mock.patch.object(self.module, "validate_external_action_pins"),
        ):
            return self.module.main()

    def test_declared_version_is_read_from_repo_spec(self) -> None:
        self.assertRegex(self.declared, r"^\d+\.\d+\.\d+$")
        text = (ROOT / ".l9/repo-spec.yaml").read_text(encoding="utf-8")
        self.assertIn(f"version: {self.declared}", text)

    def test_exact_tag_matching_repo_spec_passes_without_override(self) -> None:
        self.assertEqual(0, self.run_main(f"v{self.declared}"))

    def test_override_must_still_match_repo_spec(self) -> None:
        self.assertEqual(0, self.run_main(f"v{self.declared}", self.declared))
        self.assertEqual(2, self.run_main("v99.0.0", "99.0.0"))

    def test_tag_that_disagrees_with_repo_spec_fails_closed(self) -> None:
        self.assertEqual(2, self.run_main("v99.0.0"))

    def test_moving_major_alias_is_not_a_release(self) -> None:
        self.assertEqual(2, self.run_main("v2"))

    def test_missing_release_plane_contract_fails_closed(self) -> None:
        (self.tmp / ".l9/release-plane.yaml").unlink()
        self.assertEqual(2, self.run_main(f"v{self.declared}"))

    def test_release_plane_with_runtime_authority_fails_closed(self) -> None:
        path = self.tmp / ".l9/release-plane.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "runtime_authority: false", "runtime_authority: true"
            ),
            encoding="utf-8",
        )
        self.assertEqual(2, self.run_main(f"v{self.declared}"))

    def test_architecture_without_production_channel_fails_closed(self) -> None:
        path = self.tmp / ".l9/architecture.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("production_channel:", "channel:"),
            encoding="utf-8",
        )
        self.assertEqual(2, self.run_main(f"v{self.declared}"))

    def test_current_tree_contracts_satisfy_the_validator(self) -> None:
        self.module.validate_contracts(ROOT, self.declared)

    def test_current_tree_action_pins_satisfy_the_validator(self) -> None:
        """Real workflows pin as ``owner/action@<sha> # vX.Y.Z``.

        The trailing version comment is convention, not part of the
        reference; a validator that fails to strip it rejects every correctly
        pinned action and therefore every release tag.
        """
        self.module.validate_external_action_pins(ROOT)

    def test_mutable_action_reference_fails_closed(self) -> None:
        workflows = self.tmp / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "bad.yml").write_text(
            "jobs:\n  j:\n    steps:\n"
            "      - uses: actions/checkout@v4 # floating\n"
            "      - uses: ./.github/actions/local\n",
            encoding="utf-8",
        )
        with self.assertRaises(self.module.ReleaseError) as caught:
            self.module.validate_external_action_pins(self.tmp)
        self.assertIn("actions/checkout@v4", str(caught.exception))

    def test_commented_full_sha_reference_is_accepted(self) -> None:
        workflows = self.tmp / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "good.yml").write_text(
            "jobs:\n  j:\n    steps:\n"
            f"      - uses: actions/checkout@{'a' * 40} # v4.2.0\n",
            encoding="utf-8",
        )
        self.module.validate_external_action_pins(self.tmp)


class GitHubYamlSurfaceDiscoveryTests(unittest.TestCase):
    """``.yml`` and ``.yaml`` are the same executable surface to GitHub.

    A workflow or composite action loads from either spelling, so a pin scan
    that walks only one extension leaves the other free to carry a mutable
    external reference into an immutable release. These tests drive the real
    validator rather than re-implementing its parsing.
    """

    STEP = "jobs:\n  j:\n    steps:\n      - uses: {reference}\n"
    ACTION = "runs:\n  using: composite\n  steps:\n    - uses: {reference}\n"
    MUTABLE = "actions/checkout@v4"
    BRANCH = "actions/checkout@main"
    PINNED = f"actions/checkout@{'0123456789' * 4}"

    def setUp(self) -> None:
        self.module = load_validator()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, relative: str, body: str) -> pathlib.Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def assert_rejected(self, relative: str, body: str, reference: str) -> None:
        self.write(relative, body)
        with self.assertRaises(self.module.ReleaseError) as caught:
            self.module.validate_external_action_pins(self.tmp)
        message = str(caught.exception)
        self.assertIn(reference, message)
        self.assertIn(relative, message)

    def test_discovery_returns_both_extensions_once_each(self) -> None:
        expected = {
            self.write(".github/workflows/a.yml", "on: push\n"),
            self.write(".github/workflows/b.yaml", "on: push\n"),
            self.write(".github/actions/c/action.yml", "runs:\n"),
            self.write(".github/actions/d/action.yaml", "runs:\n"),
            self.write(".github/governance/rule-modes.yaml", "modes: {}\n"),
        }
        self.write(".github/notes.md", "uses: actions/checkout@v4\n")
        surfaces = self.module.github_yaml_surfaces(self.tmp)
        self.assertEqual(expected, set(surfaces))
        self.assertEqual(len(surfaces), len(set(surfaces)))
        self.assertEqual(sorted(surfaces), surfaces)

    def test_discovery_is_empty_without_a_github_directory(self) -> None:
        self.assertEqual([], self.module.github_yaml_surfaces(self.tmp))

    def test_mutable_reference_in_yml_workflow_fails_closed(self) -> None:
        self.assert_rejected(
            ".github/workflows/w.yml",
            self.STEP.format(reference=self.MUTABLE),
            self.MUTABLE,
        )

    def test_mutable_reference_in_yaml_workflow_fails_closed(self) -> None:
        self.assert_rejected(
            ".github/workflows/w.yaml",
            self.STEP.format(reference=self.MUTABLE),
            self.MUTABLE,
        )

    def test_branch_reference_in_yaml_workflow_fails_closed(self) -> None:
        self.assert_rejected(
            ".github/workflows/w.yaml",
            self.STEP.format(reference=self.BRANCH),
            self.BRANCH,
        )

    def test_mutable_reference_in_yml_action_fails_closed(self) -> None:
        self.assert_rejected(
            ".github/actions/a/action.yml",
            self.ACTION.format(reference=self.MUTABLE),
            self.MUTABLE,
        )

    def test_mutable_reference_in_yaml_action_fails_closed(self) -> None:
        self.assert_rejected(
            ".github/actions/a/action.yaml",
            self.ACTION.format(reference=self.MUTABLE),
            self.MUTABLE,
        )

    def test_mutable_reference_in_nested_yaml_surface_fails_closed(self) -> None:
        self.assert_rejected(
            ".github/workflows/nested/deep/w.yaml",
            self.STEP.format(reference=self.MUTABLE),
            self.MUTABLE,
        )

    def test_full_sha_in_yaml_workflow_passes(self) -> None:
        self.write(".github/workflows/w.yaml", self.STEP.format(reference=self.PINNED))
        self.module.validate_external_action_pins(self.tmp)

    def test_full_sha_in_yaml_action_passes(self) -> None:
        self.write(
            ".github/actions/a/action.yaml", self.ACTION.format(reference=self.PINNED)
        )
        self.module.validate_external_action_pins(self.tmp)

    def test_local_reference_in_yaml_surface_remains_allowed(self) -> None:
        self.write(
            ".github/workflows/w.yaml",
            self.STEP.format(reference="./.github/actions/validate-release"),
        )
        self.module.validate_external_action_pins(self.tmp)


if __name__ == "__main__":
    unittest.main()
