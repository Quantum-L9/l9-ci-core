"""Consumer repositories may describe themselves but may not own CI policy."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / ".github" / "actions" / "resolve-consumer-metadata" / "resolve.py"
)
SCHEMA_PATH = ROOT / ".l9" / "ci-consumer.schema.json"

spec = importlib.util.spec_from_file_location("resolve_consumer_metadata", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConsumerMetadataTests(unittest.TestCase):
    def test_schema_exposes_only_descriptive_fields(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            {"schema", "owner", "repo_class", "waiver_refs"},
            set(schema["properties"]),
        )

    def test_missing_metadata_is_valid_auto_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".l9" / "ci.json"
            self.assertIsNone(module.load_metadata(path))

    def test_minimal_metadata_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".l9" / "ci.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema": "l9.ci-consumer/v1",
                        "owner": "Quantum-L9/platform",
                        "repo_class": "python",
                        "waiver_refs": ["WAIVER-001"],
                    }
                ),
                encoding="utf-8",
            )
            payload = module.load_metadata(path)
        assert payload is not None
        self.assertEqual("Quantum-L9/platform", payload["owner"])
        self.assertEqual("python", payload["repo_class"])
        self.assertEqual(["WAIVER-001"], payload["waiver_refs"])

    def test_policy_authority_keys_fail_closed(self) -> None:
        forbidden = (
            "mode",
            "providers",
            "sdk_revision",
            "core_revision",
            "tool_versions",
            "permissions",
            "workflow",
            "policy",
        )
        for key in forbidden:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "ci.json"
                path.write_text(
                    json.dumps(
                        {"schema": "l9.ci-consumer/v1", key: "forbidden"}
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(module.ConsumerMetadataError):
                    module.load_metadata(path)

    def test_path_cannot_escape_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with unittest.mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": temp},
                clear=False,
            ):
                with self.assertRaises(module.ConsumerMetadataError):
                    module.workspace_path("../ci.json")


if __name__ == "__main__":
    unittest.main()
