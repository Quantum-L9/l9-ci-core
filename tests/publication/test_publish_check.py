from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/publish-check/publish.py"
spec = importlib.util.spec_from_file_location("publish_check", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublishCheckTests(unittest.TestCase):
    def publication(self) -> dict:
        return {
            "schema": "l9.core-publication/v1",
            "name": "L9 review",
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "success",
            "output": {
                "title": "L9 review",
                "summary": "summary",
                "text": "",
                "annotations": [],
            },
            "metadata": {"run_url": "https://example.invalid/run"},
        }

    def test_valid_publication_document(self) -> None:
        document = self.publication()
        self.assertIs(document, module.validate_document(document))

    def test_too_many_annotations_are_rejected(self) -> None:
        document = self.publication()
        document["output"]["annotations"] = [{} for _ in range(51)]
        with self.assertRaises(module.CheckPublicationError):
            module.validate_document(document)

    def test_sarif_preflight_checks_only_the_projection_envelope(self) -> None:
        document = {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            # Core deliberately does not inspect SDK-owned runs or results.
            "runs": [
                {
                    "tool": {"driver": {"name": "l9-ci-sdk"}},
                    "results": "opaque-to-core",
                }
            ],
        }
        self.assertIs(document, module.validate_sarif_envelope(document))

    def test_external_identity_is_stable_across_run_attempts(self) -> None:
        expected = "l9.core-publication/v1:12345:pr-semgrep"
        self.assertEqual(expected, module.external_identity("12345", "pr-semgrep"))
        self.assertNotIn("attempt", expected)

    def test_invalid_sarif_fails_before_any_api_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            publication = workspace / "publication.json"
            sarif = workspace / "results.sarif"
            publication.write_text(json.dumps(self.publication()), encoding="utf-8")
            sarif.write_text(
                json.dumps({"version": "2.0.0", "runs": []}),
                encoding="utf-8",
            )
            environment = {
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_TOKEN": "unused",
                "L9_REPOSITORY": "Quantum-L9/example",
                "L9_PUBLICATION": str(publication),
                "L9_SARIF": str(sarif),
                "L9_RUN_ID": "12345",
                "L9_MATRIX_ID": "pr-semgrep",
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(module, "api_request") as request,
            ):
                self.assertEqual(2, module.main())
            request.assert_not_called()

    def test_first_attempt_creates_check_with_external_identity(self) -> None:
        identity = module.external_identity("12345", "pr-semgrep")
        with patch.object(
            module,
            "api_request",
            side_effect=[
                {"check_runs": []},
                {"id": 41, "html_url": "https://example.invalid/check/41"},
            ],
        ) as request:
            result, operation = module.publish_check(
                "token",
                "Quantum-L9/example",
                self.publication(),
                identity,
            )
        self.assertEqual("created", operation)
        self.assertEqual(41, result["id"])
        self.assertEqual("GET", request.call_args_list[0].args[1])
        self.assertEqual("POST", request.call_args_list[1].args[1])
        self.assertEqual(identity, request.call_args_list[1].args[3]["external_id"])

    def test_retry_updates_check_with_same_external_identity(self) -> None:
        identity = module.external_identity("12345", "pr-semgrep")
        with patch.object(
            module,
            "api_request",
            side_effect=[
                {"check_runs": [{"id": 41, "external_id": identity}]},
                {"id": 41, "html_url": "https://example.invalid/check/41"},
            ],
        ) as request:
            result, operation = module.publish_check(
                "token",
                "Quantum-L9/example",
                self.publication(),
                identity,
            )
        self.assertEqual("updated", operation)
        self.assertEqual(41, result["id"])
        self.assertEqual("PATCH", request.call_args_list[1].args[1])
        self.assertTrue(request.call_args_list[1].args[2].endswith("/check-runs/41"))
        self.assertNotIn("head_sha", request.call_args_list[1].args[3])
        self.assertEqual(identity, request.call_args_list[1].args[3]["external_id"])


if __name__ == "__main__":
    unittest.main()
