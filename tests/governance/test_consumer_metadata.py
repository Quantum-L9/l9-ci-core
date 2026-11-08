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
MODULE_PATH = ROOT / ".github" / "actions" / "resolve-consumer-metadata" / "metadata.py"
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
            {"schema", "owner", "repo_class", "waiver_refs", "product_kind"},
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
                    json.dumps({"schema": "l9.ci-consumer/v1", key: "forbidden"}),
                    encoding="utf-8",
                )
                with self.assertRaises(module.ConsumerMetadataError):
                    module.load_metadata(path)

    def _write(self, temp: str, payload: dict[str, object]) -> Path:
        path = Path(temp) / "ci.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_product_kind_defaults_to_undeclared(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            payload = module.load_metadata(
                self._write(temp, {"schema": "l9.ci-consumer/v1"})
            )
        assert payload is not None
        self.assertEqual("", payload["product_kind"])

    def test_admitted_product_kinds_load(self) -> None:
        for kind in ("node", "dependency"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                payload = module.load_metadata(
                    self._write(
                        temp, {"schema": "l9.ci-consumer/v1", "product_kind": kind}
                    )
                )
                assert payload is not None
                self.assertEqual(kind, payload["product_kind"])

    def test_unadmitted_product_kind_fails_closed(self) -> None:
        # `sdk` is an unresolved ProductKind in Quantum-L9/.github and is not
        # admitted; a non-string value is never a kind.
        for kind in ("sdk", "gate", "Node", "", 0, 1, None, ["node"]):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                path = self._write(
                    temp, {"schema": "l9.ci-consumer/v1", "product_kind": kind}
                )
                with self.assertRaises(module.ConsumerMetadataError):
                    module.load_metadata(path)

    def test_schema_product_kinds_match_the_validator(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            module.ALLOWED_PRODUCT_KINDS,
            set(schema["properties"]["product_kind"]["enum"]),
        )

    def test_main_emits_product_kind(self) -> None:
        cases = (
            (None, "product-kind="),
            ({"schema": "l9.ci-consumer/v1"}, "product-kind="),
            (
                {"schema": "l9.ci-consumer/v1", "product_kind": "node"},
                "product-kind=node",
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                if payload is not None:
                    target = Path(temp) / ".l9" / "ci.json"
                    target.parent.mkdir()
                    target.write_text(json.dumps(payload), encoding="utf-8")
                output = Path(temp) / "github_output"
                with unittest.mock.patch.dict(
                    os.environ,
                    {"GITHUB_WORKSPACE": temp, "GITHUB_OUTPUT": str(output)},
                    clear=False,
                ):
                    os.environ.pop("L9_CONSUMER_METADATA", None)
                    self.assertEqual(0, module.main())
                self.assertIn(expected, output.read_text(encoding="utf-8").splitlines())

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
