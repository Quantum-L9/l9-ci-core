from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ArtifactProtocolTests(unittest.TestCase):
    def test_protocol_preserves_sdk_ownership(self) -> None:
        text = (ROOT / ".l9/artifact-protocol.yaml").read_text(encoding="utf-8")
        required = (
            "Core must validate a canonical bundle before routing or upload.",
            "Core must not merge canonical bundles.",
            "Core must not infer compatibility from JSON shape.",
            "SDK exit codes must propagate without remapping.",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, text)

    def test_phase_2_actions_exist(self) -> None:
        expected = {
            "provision-sdk",
            "invoke-sdk",
            "validate-bundle",
            "route-artifacts",
            "build-artifact-manifest",
        }
        actual = {
            path.name for path in (ROOT / ".github/actions").iterdir() if path.is_dir()
        }
        self.assertTrue(expected.issubset(actual))


if __name__ == "__main__":
    unittest.main()
