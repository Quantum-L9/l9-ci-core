from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / ".l9" / "sdk-compatibility.yaml"
EXPECTED_SHA = "c78486ea9b7d596d0b6db755b5780e5289878d35"


class CompatibilityManifestTests(unittest.TestCase):
    def test_manifest_locks_the_sdk_revision(self) -> None:
        text = MANIFEST.read_text(encoding="utf-8")
        self.assertIn("schema: l9.sdk-compatibility/v1", text)
        self.assertIn(EXPECTED_SHA, text)
        self.assertIn("l9.integration-contract/v1", text)

    def test_manifest_disables_drift_mechanisms(self) -> None:
        text = MANIFEST.read_text(encoding="utf-8")
        required = (
            "arbitrary_install_commands_allowed: false",
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
