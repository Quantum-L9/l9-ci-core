from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / ".github/actions/build-artifact-manifest/manifest.py"
RETRIEVE_PATH = ROOT / ".github/actions/retrieve-artifacts/retrieve.py"
PREPARE_PATH = ROOT / ".github/actions/retrieve-artifacts/prepare.py"
REVISION = "1" * 40
SDK_REVISION = "2" * 40
ARTIFACT_NAME = "l9-semgrep-python-3.12-123-1"


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest = load("build_artifact_manifest", MANIFEST_PATH)
retrieve = load("retrieve_artifacts", RETRIEVE_PATH)
prepare = load("prepare_artifacts", PREPARE_PATH)


class ArtifactIntegrityTests(unittest.TestCase):
    def create_tree(self, workspace: Path) -> dict[str, str]:
        root = workspace / "artifacts"
        raw = root / "raw/semgrep/python-3.12/report.json"
        bundle = root / "l9/python-3.12/finding-bundle.json"
        payload = root / "l9/python-3.12/agent-review-payload.json"
        sarif = root / "l9/python-3.12/results.sarif"
        gate = root / "l9/python-3.12/gate-result.json"
        capabilities = root / "l9/python-3.12/repository-capabilities.json"
        routing = root / "metadata/python-3.12/routing-record.json"
        for path, content in (
            (raw, b'{"raw":true}\n'),
            (bundle, b'{"bundle":true}\n'),
            (payload, b'{"payload":true}\n'),
            (sarif, b'{"version":"2.1.0","runs":[]}\n'),
            (gate, b'{"status":"pass"}\n'),
            (capabilities, b'{"languages":["python"]}\n'),
            (routing, b'{"schema":"l9.core-routing-record/v2"}\n'),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return {
            "GITHUB_WORKSPACE": str(workspace),
            "L9_PROVIDER": "semgrep",
            "L9_MATRIX_ID": "python-3.12",
            "L9_ARTIFACT_NAME": ARTIFACT_NAME,
            "L9_REPOSITORY": "Quantum-L9/example",
            "L9_REPOSITORY_REVISION": REVISION,
            "L9_SDK_REVISION": SDK_REVISION,
            "L9_ARTIFACT_ROOT": "artifacts",
            "L9_BUNDLE": "artifacts/l9/python-3.12/finding-bundle.json",
            "L9_AGENT_PAYLOAD": ("artifacts/l9/python-3.12/agent-review-payload.json"),
            "L9_RAW_DIRECTORY": "artifacts/raw/semgrep/python-3.12",
            "L9_ROUTING_RECORD": ("artifacts/metadata/python-3.12/routing-record.json"),
            "L9_MANIFEST_OUTPUT": (
                "artifacts/metadata/python-3.12/artifact-index.json"
            ),
        }

    def build(self, workspace: Path) -> tuple[dict[str, str], dict[str, object]]:
        environment = self.create_tree(workspace)
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(0, manifest.main())
        index_path = workspace / "artifacts/metadata/python-3.12/artifact-index.json"
        return environment, json.loads(index_path.read_text(encoding="utf-8"))

    def retrieve_environment(self, workspace: Path) -> dict[str, str]:
        return {
            "GITHUB_WORKSPACE": str(workspace),
            "L9_PROVIDER": "semgrep",
            "L9_MATRIX_ID": "python-3.12",
            "L9_ARTIFACT_NAME": ARTIFACT_NAME,
            "L9_REPOSITORY": "Quantum-L9/example",
            "L9_REPOSITORY_REVISION": REVISION,
            "L9_SDK_REVISION": SDK_REVISION,
            "L9_ARTIFACT_ROOT": "artifacts",
        }

    def test_index_is_complete_relocatable_and_self_excluding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, index = self.build(workspace)
            self.assertEqual("l9.core-artifact-index/v1", index["schema"])
            self.assertEqual(".", index["artifact_root"])
            self.assertEqual("sha256", index["hash_algorithm"])
            self.assertEqual(ARTIFACT_NAME, index["artifact_name"])
            self.assertEqual(
                "metadata/python-3.12/artifact-index.json",
                index["index_path"],
            )
            entries = index["entries"]
            assert isinstance(entries, list)
            paths = [entry["path"] for entry in entries]
            self.assertEqual(sorted(paths), paths)
            self.assertNotIn(index["index_path"], paths)
            self.assertEqual(
                {
                    "l9/python-3.12/agent-review-payload.json",
                    "l9/python-3.12/finding-bundle.json",
                    "l9/python-3.12/gate-result.json",
                    "l9/python-3.12/repository-capabilities.json",
                    "l9/python-3.12/results.sarif",
                    "metadata/python-3.12/routing-record.json",
                    "raw/semgrep/python-3.12/report.json",
                },
                set(paths),
            )
            for entry in entries:
                self.assertFalse(Path(entry["path"]).is_absolute())
                path = workspace / "artifacts" / entry["path"]
                self.assertEqual(path.stat().st_size, entry["size"])
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    entry["sha256"],
                )

    def test_index_rejects_output_outside_fixed_metadata_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.create_tree(workspace)
            environment["L9_MANIFEST_OUTPUT"] = "artifacts/index.json"
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(2, manifest.main())

    def test_index_rejects_symlinked_artifact_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.create_tree(workspace)
            outside = workspace / "outside.json"
            outside.write_text("{}\n", encoding="utf-8")
            link = workspace / "artifacts/raw/semgrep/python-3.12/link.json"
            link.symlink_to(outside)
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(2, manifest.main())

    def test_retrieval_verifies_and_emits_safe_routes_after_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.build(workspace)
            relocated = workspace / "downloaded"
            (workspace / "artifacts").rename(relocated)
            output = workspace / "outputs"
            environment = self.retrieve_environment(workspace)
            environment["L9_ARTIFACT_ROOT"] = "downloaded"
            environment["GITHUB_OUTPUT"] = str(output)
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(0, retrieve.main())
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(
                "downloaded/l9/python-3.12/finding-bundle.json",
                values["bundle"],
            )
            self.assertEqual(
                "downloaded/l9/python-3.12/results.sarif",
                values["sarif"],
            )
            self.assertEqual(
                "downloaded/metadata/python-3.12/routing-record.json",
                values["routing-record"],
            )

    def test_retrieval_rejects_tampering_and_unindexed_files(self) -> None:
        for mutation in ("tamper", "extra"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                self.build(workspace)
                if mutation == "tamper":
                    target = (
                        workspace / "artifacts/l9/python-3.12/agent-review-payload.json"
                    )
                    target.write_bytes(b'{"payload":"tampered"}\n')
                else:
                    (workspace / "artifacts/unindexed.txt").write_text(
                        "surprise\n",
                        encoding="utf-8",
                    )
                with patch.dict(
                    os.environ,
                    self.retrieve_environment(workspace),
                    clear=True,
                ):
                    self.assertEqual(2, retrieve.main())

    def test_retrieval_binds_index_to_requested_identity(self) -> None:
        fields = {
            "L9_ARTIFACT_NAME": "l9-semgrep-python-3.12-999-1",
            "L9_REPOSITORY": "Quantum-L9/other",
            "L9_REPOSITORY_REVISION": "3" * 40,
            "L9_SDK_REVISION": "4" * 40,
            "L9_PROVIDER": "other-provider",
            "L9_MATRIX_ID": "other-matrix",
        }
        for name, value in fields.items():
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                self.build(workspace)
                environment = self.retrieve_environment(workspace)
                environment[name] = value
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(2, retrieve.main())

    def test_retrieval_rejects_route_not_bound_to_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.build(workspace)
            index_path = (
                workspace / "artifacts/metadata/python-3.12/artifact-index.json"
            )
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["routes"]["finding_bundle"] = (
                "l9/python-3.12/agent-review-payload.json"
            )
            index_path.write_text(json.dumps(index), encoding="utf-8")
            with patch.dict(
                os.environ,
                self.retrieve_environment(workspace),
                clear=True,
            ):
                self.assertEqual(2, retrieve.main())

    def test_prepare_requires_empty_non_symlink_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            destination = workspace / "download"
            environment = {
                "GITHUB_WORKSPACE": str(workspace),
                "L9_DESTINATION": "download",
            }
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(0, prepare.main())
                self.assertEqual(0, prepare.main())
            (destination / "stale").write_text("stale\n", encoding="utf-8")
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(2, prepare.main())

            outside = workspace.parent / f"{workspace.name}-outside"
            outside.mkdir()
            link = workspace / "linked"
            link.symlink_to(outside, target_is_directory=True)
            try:
                environment["L9_DESTINATION"] = "linked/download"
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(2, prepare.main())
            finally:
                link.unlink()
                outside.rmdir()


if __name__ == "__main__":
    unittest.main()
