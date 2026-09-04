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
from unittest import mock

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
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(self.module, "run_tests"),
            mock.patch.object(self.module, "validate_external_action_pins"),
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


if __name__ == "__main__":
    unittest.main()
