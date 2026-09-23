from __future__ import annotations
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ArtifactProtocolTests(unittest.TestCase):
    def test_protocol_preserves_sdk_ownership(self) -> None:
        text = (ROOT / ".l9/artifact-protocol.yaml").read_text(encoding="utf-8")
        required = (
            "Core must validate a canonical bundle before routing or upload.",
            "Core must not merge canonical bundles.",
            "Core must not infer compatibility from JSON shape.",
            "SDK exit codes must propagate without remapping.",
            "Core must index the complete routed upload tree before upload.",
            "Core retrieval must verify the complete index before exposing any route.",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, text)

        protocol = yaml.safe_load(text)
        index = protocol["integrity_index"]
        self.assertEqual("l9.core-artifact-index/v1", index["schema"])
        self.assertEqual("sha256", index["algorithm"])
        self.assertFalse(index["relocatability"]["absolute_paths_allowed"])
        self.assertFalse(index["relocatability"]["symlinks_allowed"])
        self.assertIn("excludes itself", index["self_reference"])
        self.assertIn("never parses", index["semantic_scope"])

    def test_phase_2_actions_exist(self) -> None:
        expected = {
            "provision-sdk",
            "invoke-sdk",
            "validate-bundle",
            "route-artifacts",
            "build-artifact-manifest",
            "retrieve-artifacts",
        }
        actual = {
            path.name for path in (ROOT / ".github/actions").iterdir() if path.is_dir()
        }
        self.assertTrue(expected.issubset(actual))


if __name__ == "__main__":
    unittest.main()
