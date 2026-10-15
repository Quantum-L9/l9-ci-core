from __future__ import annotations

import datetime as dt
import email.message
import importlib.util
import json
import os
import tempfile
import unittest
import urllib.request
import urllib.response
from io import BytesIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HANDOFF_PATH = ROOT / ".github/actions/create-artifact-handoff/handoff.py"
PREPARE_PATH = ROOT / ".github/actions/retrieve-artifacts/prepare.py"
SERVER_PATH = ROOT / ".github/actions/retrieve-artifacts/verify_server.py"
REVISION = "1" * 40
SDK_REVISION = "2" * 40
HEAD_SHA = "6" * 40
ARCHIVE_DIGEST = "3" * 64
ARTIFACT_NAME = "l9-semgrep-python-3.12-123-1"


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


handoff = load("create_artifact_handoff", HANDOFF_PATH)
prepare = load("prepare_artifact_handoff", PREPARE_PATH)
server = load("verify_artifact_server", SERVER_PATH)


def document() -> dict[str, object]:
    return {
        "schema": "l9.core-artifact-handoff/v1",
        "producer": {
            "repository": "Quantum-L9/example",
            "run_id": 123,
            "head_sha": HEAD_SHA,
        },
        "artifact": {
            "id": 456,
            "name": ARTIFACT_NAME,
            "archive_digest": {
                "algorithm": "sha256",
                "value": ARCHIVE_DIGEST,
            },
        },
        "subject": {
            "repository": "Quantum-L9/example",
            "revision": REVISION,
        },
        "provider": "semgrep",
        "matrix_id": "python-3.12",
        "sdk": {
            "integration_contract": "l9.integration-contract/v1",
            "repository": "Quantum-L9/l9-ci-sdk",
            "revision": SDK_REVISION,
        },
    }


def canonical_descriptor(value: dict[str, object] | None = None) -> str:
    return json.dumps(value or document(), sort_keys=True, separators=(",", ":"))


class ArtifactHandoffProducerTests(unittest.TestCase):
    def environment(self, workspace: Path) -> dict[str, str]:
        return {
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_OUTPUT": str(workspace / "outputs"),
            "L9_ARTIFACT_ID": "456",
            "L9_ARTIFACT_NAME": ARTIFACT_NAME,
            "L9_ARTIFACT_DIGEST": f"sha256:{ARCHIVE_DIGEST}",
            "L9_PROVIDER": "semgrep",
            "L9_MATRIX_ID": "python-3.12",
            "L9_REPOSITORY": "Quantum-L9/example",
            "L9_REPOSITORY_REVISION": REVISION,
            "L9_SDK_REVISION": SDK_REVISION,
            "L9_PRODUCER_REPOSITORY": "Quantum-L9/example",
            "L9_PRODUCER_RUN_ID": "123",
            "L9_WORKFLOW_HEAD_SHA": HEAD_SHA,
            "L9_HANDOFF_OUTPUT": ".l9/runtime/handoffs/python-3.12.json",
        }

    def test_emits_closed_canonical_descriptor_from_upload_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.environment(workspace)
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(0, handoff.main())

            path = workspace / environment["L9_HANDOFF_OUTPUT"]
            content = path.read_text(encoding="utf-8")
            self.assertEqual(canonical_descriptor() + "\n", content)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            outputs = dict(
                line.split("=", 1)
                for line in (workspace / "outputs")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            self.assertEqual(canonical_descriptor(), outputs["descriptor"])
            self.assertEqual(
                environment["L9_HANDOFF_OUTPUT"], outputs["descriptor-path"]
            )
            self.assertRegex(outputs["descriptor-digest"], r"^[0-9a-f]{64}$")

    def test_rejects_missing_malformed_or_unsafe_inputs(self) -> None:
        mutations = {
            "missing upload id": ("L9_ARTIFACT_ID", ""),
            "malformed upload id": ("L9_ARTIFACT_ID", "0"),
            "malformed digest": ("L9_ARTIFACT_DIGEST", "sha256:nope"),
            "malformed repository": ("L9_REPOSITORY", "not-a-repository"),
            "malformed revision": ("L9_REPOSITORY_REVISION", "main"),
            "malformed workflow head": ("L9_WORKFLOW_HEAD_SHA", "main"),
            "unsafe destination": ("L9_HANDOFF_OUTPUT", "../handoff.json"),
            "noncanonical destination": ("L9_HANDOFF_OUTPUT", "a//handoff.json"),
        }
        for label, (name, value) in mutations.items():
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                environment = self.environment(workspace)
                environment[name] = value
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(2, handoff.main())
                self.assertFalse(
                    (workspace / ".l9/runtime/handoffs/python-3.12.json").exists()
                )

    def test_rejects_stale_or_symlinked_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.environment(workspace)
            stale = workspace / environment["L9_HANDOFF_OUTPUT"]
            stale.parent.mkdir(parents=True)
            stale.write_text("stale\n", encoding="utf-8")
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(2, handoff.main())
            self.assertEqual("stale\n", stale.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.environment(workspace)
            outside = workspace / "outside"
            outside.mkdir()
            (workspace / ".l9").symlink_to(outside, target_is_directory=True)
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(2, handoff.main())
            self.assertEqual([], list(outside.iterdir()))


class ArtifactHandoffPreparationTests(unittest.TestCase):
    def current_environment(self, workspace: Path) -> dict[str, str]:
        return {
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_OUTPUT": str(workspace / "outputs"),
            "L9_DESTINATION": "download",
            "L9_ARTIFACT_NAME": ARTIFACT_NAME,
            "L9_HANDOFF_DESCRIPTOR": "",
            "L9_TOKEN": "",
            "L9_MATRIX_ID": "python-3.12",
            "L9_PROVIDER": "semgrep",
            "L9_REPOSITORY": "Quantum-L9/example",
            "L9_REPOSITORY_REVISION": REVISION,
            "L9_SDK_REVISION": SDK_REVISION,
        }

    def test_current_run_mode_preserves_exact_name_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with patch.dict(
                os.environ, self.current_environment(workspace), clear=True
            ):
                self.assertEqual(0, prepare.main())
            outputs = dict(
                line.split("=", 1)
                for line in (workspace / "outputs")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            self.assertEqual("current-run", outputs["mode"])
            self.assertEqual(ARTIFACT_NAME, outputs["artifact-name"])
            self.assertEqual("", outputs["artifact-id"])
            self.assertEqual("Quantum-L9/example", outputs["repository"])

    def test_descriptor_mode_requires_token_and_uses_only_descriptor_identities(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.current_environment(workspace)
            environment.update(
                {
                    "L9_ARTIFACT_NAME": "",
                    "L9_HANDOFF_DESCRIPTOR": canonical_descriptor(),
                    "L9_TOKEN": "token",
                    "L9_MATRIX_ID": "caller-matrix",
                    "L9_PROVIDER": "caller-provider",
                    "L9_REPOSITORY": "Caller/Repository",
                    "L9_REPOSITORY_REVISION": "4" * 40,
                    "L9_SDK_REVISION": "5" * 40,
                }
            )
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(0, prepare.main())
            outputs = dict(
                line.split("=", 1)
                for line in (workspace / "outputs")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            self.assertEqual("descriptor", outputs["mode"])
            self.assertEqual("456", outputs["artifact-id"])
            self.assertEqual("123", outputs["source-run-id"])
            self.assertEqual("Quantum-L9/example", outputs["source-repository"])
            self.assertEqual("python-3.12", outputs["matrix-id"])
            self.assertEqual("semgrep", outputs["provider"])
            self.assertEqual(REVISION, outputs["repository-revision"])
            self.assertEqual(HEAD_SHA, outputs["workflow-head-sha"])
            self.assertEqual(SDK_REVISION, outputs["sdk-revision"])

            environment["L9_DESTINATION"] = "download-two"
            environment["L9_TOKEN"] = ""
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(2, prepare.main())

    def test_modes_are_mutually_exclusive_and_descriptor_is_closed_canonical(
        self,
    ) -> None:
        invalid = []
        extra = document()
        extra["source_url"] = "https://example.invalid/artifact"
        invalid.append(canonical_descriptor(extra))
        invalid.append(canonical_descriptor() + "\n")
        invalid.append('{"schema":"l9.core-artifact-handoff/v2"}')
        for number, descriptor in enumerate(invalid):
            with (
                self.subTest(number=number),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                environment = self.current_environment(workspace)
                environment.update(
                    {
                        "L9_ARTIFACT_NAME": "",
                        "L9_HANDOFF_DESCRIPTOR": descriptor,
                        "L9_TOKEN": "token",
                    }
                )
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(2, prepare.main())

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.current_environment(workspace)
            environment["L9_HANDOFF_DESCRIPTOR"] = canonical_descriptor()
            environment["L9_TOKEN"] = "token"
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(2, prepare.main())


class ArtifactHandoffServerTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        return {
            "L9_ARTIFACT_ID": "456",
            "L9_ARTIFACT_NAME": ARTIFACT_NAME,
            "L9_ARCHIVE_DIGEST": ARCHIVE_DIGEST,
            "L9_SOURCE_RUN_ID": "123",
            "L9_REPOSITORY_REVISION": REVISION,
            "L9_WORKFLOW_HEAD_SHA": HEAD_SHA,
        }

    def metadata(self) -> dict[str, object]:
        endpoint = (
            "https://api.github.com/repos/Quantum-L9/example/actions/artifacts/456"
        )
        return {
            "id": 456,
            "name": ARTIFACT_NAME,
            "url": endpoint,
            "archive_download_url": f"{endpoint}/zip",
            "expired": False,
            "created_at": "2026-09-20T00:00:00Z",
            "updated_at": "2026-09-20T00:01:00Z",
            "expires_at": "2026-10-20T00:00:00Z",
            "digest": f"sha256:{ARCHIVE_DIGEST}",
            "workflow_run": {"id": 123, "head_sha": HEAD_SHA},
        }

    def test_server_metadata_verifies_identity_lifecycle_and_digest(self) -> None:
        """Workflow head may differ from the analyzed subject revision."""
        endpoint = (
            "https://api.github.com/repos/Quantum-L9/example/actions/artifacts/456"
        )
        now = dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc)
        with patch.dict(os.environ, self.environment(), clear=True):
            server.verify_metadata(self.metadata(), metadata_endpoint=endpoint, now=now)

    def test_server_metadata_mismatches_fail_closed(self) -> None:
        mutations = {
            "id": ("id", 999),
            "name": ("name", "other"),
            "digest": ("digest", f"sha256:{'4' * 64}"),
            "expired": ("expired", True),
            "url": (
                "url",
                "https://api.github.com/repos/Other/repo/actions/artifacts/456",
            ),
            "archive": ("archive_download_url", "https://example.invalid/archive"),
            "expiration": ("expires_at", "2026-09-21T00:00:00Z"),
        }
        endpoint = (
            "https://api.github.com/repos/Quantum-L9/example/actions/artifacts/456"
        )
        now = dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc)
        for label, (field, value) in mutations.items():
            with self.subTest(label=label):
                metadata = self.metadata()
                metadata[field] = value
                with patch.dict(os.environ, self.environment(), clear=True):
                    with self.assertRaises(server.ServerVerificationError):
                        server.verify_metadata(
                            metadata, metadata_endpoint=endpoint, now=now
                        )

        for field, value in (("id", 999), ("head_sha", "4" * 40)):
            with self.subTest(workflow_field=field):
                metadata = self.metadata()
                workflow_run = dict(metadata["workflow_run"])
                workflow_run[field] = value
                metadata["workflow_run"] = workflow_run
                with patch.dict(os.environ, self.environment(), clear=True):
                    with self.assertRaises(server.ServerVerificationError):
                        server.verify_metadata(
                            metadata, metadata_endpoint=endpoint, now=now
                        )

    def test_metadata_url_is_derived_without_caller_artifact_url(self) -> None:
        self.assertEqual(
            "https://api.github.com/repos/Quantum-L9/example/actions/artifacts/456",
            server.metadata_url("https://api.github.com", "Quantum-L9/example", "456"),
        )
        with self.assertRaises(server.ServerVerificationError):
            server.metadata_url("http://api.github.com", "Quantum-L9/example", "456")
        with self.assertRaises(server.ServerVerificationError):
            server.metadata_url("file:///etc/passwd", "Quantum-L9/example", "456")

    def test_fetch_refuses_file_url_and_any_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            secret = Path(raw) / "secret"
            secret.write_text("{}", encoding="utf-8")
            with self.assertRaises(server.ServerVerificationError) as caught:
                server.fetch_metadata(secret.as_uri(), "token")
            self.assertIn("HTTPS", str(caught.exception))

        endpoint = (
            "https://api.github.com/repos/Quantum-L9/example/actions/artifacts/456"
        )
        location = "https://evil.example/stolen"

        def redirecting_https(handler: object, req: urllib.request.Request):
            del handler
            headers = email.message.Message()
            headers["Location"] = location
            headers["Content-Length"] = "0"
            response = urllib.response.addinfourl(
                BytesIO(b""), headers, req.full_url, 302
            )
            response.msg = headers
            return response

        with patch.object(urllib.request.HTTPSHandler, "https_open", redirecting_https):
            with self.assertRaises(server.ServerVerificationError) as caught:
                server.fetch_metadata(endpoint, "token")
        self.assertIn("refused a redirect", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
