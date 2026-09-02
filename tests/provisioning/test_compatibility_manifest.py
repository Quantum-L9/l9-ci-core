from __future__ import annotations
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / ".l9" / "sdk-compatibility.yaml"
# l9-ci-sdk on the l9.integration-contract/v1 surface, carrying the scan
# scaffolding exclusion (--exclude .l9/runtime) and the subprocess-injection
# identity mapping that central CI needs to resolve strict identity.
EXPECTED_SHA = "0efd762d1617a1c8635005d0611b1cf6f2303987"
# Retained as a tested rollback (the prior default).
ROLLBACK_SHA = "7d7762eae5e1a12fdc66276975e2949891762a20"
# Removed: two generations behind the released contract; lacks the
# `semgrep run` + `gate evaluate` handoff, so no longer an active rollback.
REMOVED_SHA = "0779fca8238011f8abea551895f96584676e9d17"


class CompatibilityManifestTests(unittest.TestCase):
    def _load(self) -> dict:
        return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

    def test_manifest_locks_the_sdk_revision(self) -> None:
        text = MANIFEST.read_text(encoding="utf-8")
        self.assertIn("schema: l9.sdk-compatibility/v1", text)
        self.assertIn(f"revision: {EXPECTED_SHA}", text)
        self.assertIn("l9.integration-contract/v1", text)

    def test_default_revision_is_sdk_v1(self) -> None:
        data = self._load()
        self.assertEqual(data["default"]["revision"], EXPECTED_SHA)

    def test_v1_is_first_supported_entry(self) -> None:
        data = self._load()
        revisions = [entry["revision"] for entry in data["supported"]]
        self.assertEqual(revisions[0], EXPECTED_SHA)
        # The prior default is retained as a tested rollback.
        self.assertIn(ROLLBACK_SHA, revisions)

    def test_two_generations_behind_rollback_is_removed(self) -> None:
        text = MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn(REMOVED_SHA, text)

    def test_v1_entry_declares_the_full_cli_surface(self) -> None:
        data = self._load()
        entry = next(
            item for item in data["supported"] if item["revision"] == EXPECTED_SHA
        )
        for path in (
            "providers detect",
            "semgrep run",
            "gate evaluate",
            "bundle validate",
            "bundle project-sarif",
        ):
            with self.subTest(path=path):
                self.assertIn(path, entry["required_cli_paths"])

    def test_manifest_disables_drift_mechanisms(self) -> None:
        text = MANIFEST.read_text(encoding="utf-8")
        required = (
            "arbitrary_install_commands_allowed: false",
            "dependency_manifest_install_allowed: true",
            "floating_git_references_allowed: false",
            "short_git_revisions_allowed: false",
            "branches_allowed: false",
            "tags_allowed: false",
            "unlisted_revisions_allowed: false",
            "fallback_to_parent_allowed: false",
            "fallback_to_legacy_cli_allowed: false",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, text)


if __name__ == "__main__":
    unittest.main()
