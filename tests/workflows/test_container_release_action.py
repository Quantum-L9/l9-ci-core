from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/container-release/release.py"
spec = importlib.util.spec_from_file_location("container_release", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

COMMIT = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
IMAGE_REPOSITORY = "ghcr.io/quantum-l9/l9-cognitive-runtime"
IMAGE_REF = f"{IMAGE_REPOSITORY}@{IMAGE_DIGEST}"


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class ContainerReleaseTests(unittest.TestCase):
    def _fixture(self, workspace: Path, *, gate_status: str = "pass") -> Path:
        analysis = workspace / "analysis/l9/release-semgrep"
        analysis.mkdir(parents=True)
        (analysis / "finding-bundle.json").write_text(
            json.dumps(
                {
                    "schema": "l9.finding-bundle/v1",
                    "schema_version": "1.0.0",
                    "SDK_version": "2.0.0",
                    "snapshot": {"revision": COMMIT, "repository_root": "."},
                }
            ),
            encoding="utf-8",
        )
        (analysis / "gate-result.json").write_text(
            json.dumps(
                {
                    "schema": "l9.gate-result/v1",
                    "schema_version": "1.0.0",
                    "status": gate_status,
                }
            ),
            encoding="utf-8",
        )
        profile = workspace / ".l9/deployment.yaml"
        profile.parent.mkdir(parents=True)
        profile.write_text("schema: l9.deployment-profile/v1\n", encoding="utf-8")
        return workspace / "analysis"

    def _build(self, workspace: Path, *, gate_status: str = "pass") -> dict[str, str]:
        analysis_root = self._fixture(workspace, gate_status=gate_status)
        return module.build_release_documents(
            workspace=workspace,
            analysis_root=analysis_root,
            analysis_artifact="l9-semgrep-release-42-1",
            matrix_id="release-semgrep",
            deployment_profile=".l9/deployment.yaml",
            registered_profile_path=(
                "integrations/consumers/l9-cognitive-runtime.deployment.yaml"
            ),
            image_repository=IMAGE_REPOSITORY,
            image_digest=IMAGE_DIGEST,
            image_ref=IMAGE_REF,
            environment="staging",
            repository="Quantum-L9/l9-cognitive-runtime",
            commit_sha=COMMIT,
            ref="refs/heads/main",
            run_id=42,
            workflow_ref="Quantum-L9/l9-cognitive-runtime/.github/workflows/release.yml@refs/heads/main",
            actor="release-bot",
            now=datetime(2026, 8, 21, 13, 0, tzinfo=UTC),
        )

    def test_release_evidence_binds_sdk_verdict_and_final_artifact_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            result = self._build(workspace)
            evidence = workspace / ".l9/release/evidence"
            gate = json.loads((evidence / "ci-gate-binding.json").read_text())
            request = json.loads(
                (workspace / ".l9/release/deployment-request.json").read_text()
            )
            self.assertEqual("l9-release-evidence-42", result["artifact_name"])
            self.assertEqual("PASS", gate["status"])
            self.assertEqual(result["artifact_name"], gate["workflow"]["artifact_name"])
            self.assertEqual(
                result["artifact_name"], request["evidence"]["artifact_name"]
            )
            self.assertEqual(
                "l9-semgrep-release-42-1",
                gate["workflow"]["analysis_artifact_name"],
            )

    def test_request_uses_registered_profile_path_and_local_profile_digest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build(workspace)
            request = json.loads(
                (workspace / ".l9/release/deployment-request.json").read_text()
            )
            self.assertEqual(
                "integrations/consumers/l9-cognitive-runtime.deployment.yaml",
                request["profile"]["path"],
            )
            self.assertEqual(
                sha256_file(workspace / ".l9/deployment.yaml"),
                request["profile"]["digest"],
            )
            self.assertEqual("Quantum-L9/l9-deploy", module._DEPLOYMENT_REPOSITORY)

    def test_artifact_binding_digest_matches_l9_deploy_canonicalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build(workspace)
            path = workspace / ".l9/release/evidence/release-artifact-binding.json"
            binding = json.loads(path.read_text())
            supplied = binding.pop("binding_digest")
            self.assertEqual(module._document_digest(binding), supplied)

    def test_non_pass_sdk_gate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "SDK gate result is not pass"):
                self._build(Path(directory), gate_status="fail")

    def test_action_targets_canonical_deployment_repo_and_supports_source_arg(
        self,
    ) -> None:
        text = (ROOT / ".github/actions/container-release/action.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("repos/Quantum-L9/l9-deploy/dispatches", text)
        self.assertIn("source-revision-build-arg-name", text)
        self.assertNotIn("l9-deployment-platform/dispatches", text)


if __name__ == "__main__":
    unittest.main()
