from __future__ import annotations
import json
import unittest
from pathlib import Path

import jsonschema
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
            "Core emits a handoff descriptor only from successful immutable upload outputs.",
            "Descriptor retrieval must ignore caller source identities and URLs.",
            "Descriptor retrieval must verify GitHub metadata before immutable-ID download.",
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

        handoff = protocol["handoff_descriptor"]
        self.assertEqual("l9.core-artifact-handoff/v1", handoff["schema"])
        self.assertEqual(
            ".l9/core-artifact-handoff.schema.json", handoff["schema_document"]
        )
        self.assertTrue(handoff["closed"])
        self.assertEqual("successful_upload_artifact", handoff["emitted_after"])
        self.assertIn("does not replace", handoff["exclusions"])

        retrieval = protocol["retrieval"]
        self.assertEqual(
            "exact_name", retrieval["modes"]["current_run"]["artifact_selection"]
        )
        descriptor = retrieval["modes"]["descriptor_cross_run"]
        self.assertTrue(descriptor["token_required"])
        self.assertEqual("descriptor_only", descriptor["source_authority"])
        self.assertEqual("immutable_id", descriptor["artifact_selection"])

    def test_handoff_descriptor_schema_is_closed(self) -> None:
        schema = json.loads(
            (ROOT / ".l9/core-artifact-handoff.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        descriptor = {
            "schema": "l9.core-artifact-handoff/v1",
            "producer": {
                "repository": "Quantum-L9/example",
                "run_id": 123,
                "head_sha": "6" * 40,
            },
            "artifact": {
                "id": 456,
                "name": "l9-semgrep-python-3.12-123-1",
                "archive_digest": {"algorithm": "sha256", "value": "3" * 64},
            },
            "subject": {
                "repository": "Quantum-L9/example",
                "revision": "1" * 40,
            },
            "provider": "semgrep",
            "matrix_id": "python-3.12",
            "sdk": {
                "integration_contract": "l9.integration-contract/v1",
                "repository": "Quantum-L9/l9-ci-sdk",
                "revision": "2" * 40,
            },
        }
        jsonschema.validate(descriptor, schema)
        descriptor["source_url"] = "https://example.invalid"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(descriptor, schema)

    def test_phase_2_actions_exist(self) -> None:
        expected = {
            "provision-sdk",
            "invoke-sdk",
            "validate-bundle",
            "route-artifacts",
            "build-artifact-manifest",
            "create-artifact-handoff",
            "retrieve-artifacts",
        }
        actual = {
            path.name for path in (ROOT / ".github/actions").iterdir() if path.is_dir()
        }
        self.assertTrue(expected.issubset(actual))

    def test_handoff_transport_does_not_parse_sdk_owned_semantics(self) -> None:
        paths = (
            ROOT / ".github/actions/create-artifact-handoff/handoff.py",
            ROOT / ".github/actions/retrieve-artifacts/prepare.py",
            ROOT / ".github/actions/retrieve-artifacts/verify_server.py",
        )
        forbidden = (
            "findings",
            "severity",
            "rule_id",
            "results.sarif",
            "finding-bundle.json",
            "agent-review-payload.json",
            "report.json",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
