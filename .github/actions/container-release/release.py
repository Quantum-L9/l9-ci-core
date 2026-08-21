"""Build release evidence from the SDK-owned gate verdict and OCI digest."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

_MATRIX_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FULL_SHA = re.compile(r"^[a-f0-9]{40}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_IMAGE_REPOSITORY = re.compile(r"^ghcr\.io/[A-Za-z0-9._/-]+$")
_DEPLOYMENT_REPOSITORY = "Quantum-L9/l9-deploy"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _document_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise ValueError(f"expected object field: {key}")
    return nested


def _confined_file(workspace: Path, relative: str) -> Path:
    candidate = (workspace / relative).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise ValueError(f"path escapes workspace: {relative}")
    if not candidate.is_file():
        raise ValueError(f"required file missing: {relative}")
    return candidate


def _registered_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("registered profile path must be repository-relative")
    if not value.strip():
        raise ValueError("registered profile path is required")
    return value


def _analysis_file(root: Path, matrix_id: str, filename: str) -> Path:
    suffix = ("l9", matrix_id, filename)
    candidates = [
        path
        for path in root.rglob(filename)
        if path.is_file() and tuple(path.parts[-3:]) == suffix
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one {filename} for matrix {matrix_id}; found {len(candidates)}"
        )
    return candidates[0]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def build_release_documents(
    *,
    workspace: Path,
    analysis_root: Path,
    analysis_artifact: str,
    matrix_id: str,
    deployment_profile: str,
    registered_profile_path: str,
    image_repository: str,
    image_digest: str,
    image_ref: str,
    environment: str,
    repository: str,
    commit_sha: str,
    ref: str,
    run_id: int,
    workflow_ref: str,
    actor: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Create the exact evidence set consumed by Quantum-L9/l9-deploy."""
    root = workspace.resolve()
    analysis = analysis_root.resolve()
    if not _MATRIX_ID.fullmatch(matrix_id):
        raise ValueError("invalid matrix id")
    if not _FULL_SHA.fullmatch(commit_sha):
        raise ValueError("commit SHA must contain 40 lowercase hex characters")
    if not ref.startswith("refs/"):
        raise ValueError("source ref must start with refs/")
    if environment not in {"staging", "production"}:
        raise ValueError("environment must be staging or production")
    if not _IMAGE_REPOSITORY.fullmatch(image_repository):
        raise ValueError("image repository must be an untagged ghcr.io repository")
    if not _IMAGE_DIGEST.fullmatch(image_digest):
        raise ValueError("invalid OCI image digest")
    if image_ref != f"{image_repository}@{image_digest}":
        raise ValueError("image_ref must equal image_repository@image_digest")
    if run_id < 1:
        raise ValueError("run_id must be positive")
    profile_path = _confined_file(root, deployment_profile)
    registered_path = _registered_path(registered_profile_path)

    bundle_path = _analysis_file(analysis, matrix_id, "finding-bundle.json")
    gate_result_path = _analysis_file(analysis, matrix_id, "gate-result.json")
    bundle = _json_object(bundle_path)
    gate_result = _json_object(gate_result_path)

    if bundle.get("schema") != "l9.finding-bundle/v1":
        raise ValueError("unexpected finding bundle schema")
    schema_version = bundle.get("schema_version")
    sdk_version = bundle.get("SDK_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise ValueError("finding bundle schema_version is required")
    if not isinstance(sdk_version, str) or not sdk_version:
        raise ValueError("finding bundle SDK_version is required")
    snapshot = _mapping(bundle, "snapshot")
    if snapshot.get("revision") != commit_sha:
        raise ValueError("finding bundle revision does not match release commit")

    if gate_result.get("schema") != "l9.gate-result/v1":
        raise ValueError("unexpected gate-result schema")
    if gate_result.get("status") != "pass":
        raise ValueError("SDK gate result is not pass")

    release_root = root / ".l9/release"
    evidence_root = release_root / "evidence"
    if evidence_root.exists() and any(evidence_root.iterdir()):
        raise ValueError("release evidence directory must be empty")
    evidence_root.mkdir(parents=True, exist_ok=True)

    artifact_name = f"l9-release-evidence-{run_id}"
    created_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    created_text = created_at.isoformat().replace("+00:00", "Z")
    bundle_digest = _file_digest(bundle_path)
    gate_result_digest = _file_digest(gate_result_path)

    shutil.copyfile(bundle_path, evidence_root / "finding-bundle.json")
    shutil.copyfile(gate_result_path, evidence_root / "gate-result.json")

    gate_binding: dict[str, Any] = {
        "schema": "l9.ci-gate-binding/v1",
        "status": "PASS",
        "source": {
            "repository": repository,
            "commit_sha": commit_sha,
            "ref": ref,
        },
        "canonical": {
            "bundle_digest": bundle_digest,
            "gate_result_digest": gate_result_digest,
            "schema_version": schema_version,
            "sdk_version": sdk_version,
        },
        "workflow": {
            "artifact_name": artifact_name,
            "analysis_artifact_name": analysis_artifact,
            "run_id": run_id,
        },
    }
    gate_binding_path = evidence_root / "ci-gate-binding.json"
    _write_json(gate_binding_path, gate_binding)
    gate_binding_digest = _file_digest(gate_binding_path)

    artifact_binding: dict[str, Any] = {
        "schema": "l9.release-artifact-binding/v1",
        "binding_id": str(uuid.uuid4()),
        "source": {
            "repository": repository,
            "commit_sha": commit_sha,
            "ref": ref,
            "run_id": run_id,
        },
        "artifact": {"image_ref": image_ref, "digest": image_digest},
        "canonical": {
            "gate_binding_path": "ci-gate-binding.json",
            "gate_binding_digest": gate_binding_digest,
            "bundle_path": "finding-bundle.json",
            "bundle_digest": bundle_digest,
        },
        "workflow": {
            "repository": repository,
            "workflow_ref": workflow_ref,
            "run_id": run_id,
            "artifact_name": artifact_name,
        },
        "created_at": created_text,
    }
    artifact_binding["binding_digest"] = _document_digest(artifact_binding)
    artifact_binding_path = evidence_root / "release-artifact-binding.json"
    _write_json(artifact_binding_path, artifact_binding)
    artifact_binding_digest = _file_digest(artifact_binding_path)

    request_id = str(uuid.uuid4())
    request: dict[str, Any] = {
        "schema": "l9.deployment-request/v1",
        "request_id": request_id,
        "idempotency_key": f"release:{repository}:{commit_sha}:{environment}",
        "source": {
            "repository": repository,
            "commit_sha": commit_sha,
            "ref": ref,
            "run_id": run_id,
        },
        "artifact": {
            "image": image_repository,
            "digest": image_digest,
            "image_ref": image_ref,
            "architecture": "linux/amd64",
        },
        "profile": {
            "path": registered_path,
            "digest": _file_digest(profile_path),
        },
        "evidence": {
            "schema": "l9.release-evidence-reference/v1",
            "schema_version": schema_version,
            "sdk_version": sdk_version,
            "bundle_digest": bundle_digest,
            "gate_binding_digest": gate_binding_digest,
            "artifact_binding_digest": artifact_binding_digest,
            "workflow_run_id": run_id,
            "artifact_name": artifact_name,
            "bundle_path": "finding-bundle.json",
            "gate_binding_path": "ci-gate-binding.json",
            "artifact_binding_path": "release-artifact-binding.json",
            "provenance_reference": f"oci://{image_ref}#provenance",
            "sbom_reference": f"oci://{image_ref}#sbom",
        },
        "target": {"environment": environment},
        "requested_by": actor,
        "requested_at": created_text,
    }
    _write_json(release_root / "deployment-request.json", request)
    return {
        "artifact_name": artifact_name,
        "deployment_request_id": request_id,
        "gate_binding_digest": gate_binding_digest,
        "artifact_binding_digest": artifact_binding_digest,
        "deployment_repository": _DEPLOYMENT_REPOSITORY,
    }


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"required environment variable is missing: {name}")
    return value


def _write_output(name: str, value: str) -> None:
    output = _required("GITHUB_OUTPUT")
    with Path(output).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> int:
    try:
        workspace = Path(_required("GITHUB_WORKSPACE"))
        results = build_release_documents(
            workspace=workspace,
            analysis_root=workspace / _required("L9_ANALYSIS_ROOT"),
            analysis_artifact=_required("L9_ANALYSIS_ARTIFACT"),
            matrix_id=_required("L9_MATRIX_ID"),
            deployment_profile=_required("L9_DEPLOYMENT_PROFILE"),
            registered_profile_path=_required("L9_REGISTERED_PROFILE_PATH"),
            image_repository=_required("L9_IMAGE_REPOSITORY"),
            image_digest=_required("L9_IMAGE_DIGEST"),
            image_ref=_required("L9_IMAGE_REF"),
            environment=_required("L9_ENVIRONMENT"),
            repository=_required("GITHUB_REPOSITORY"),
            commit_sha=_required("GITHUB_SHA"),
            ref=_required("GITHUB_REF"),
            run_id=int(_required("GITHUB_RUN_ID")),
            workflow_ref=_required("GITHUB_WORKFLOW_REF"),
            actor=_required("GITHUB_ACTOR"),
        )
        _write_output("artifact-name", results["artifact_name"])
        _write_output("deployment-request-id", results["deployment_request_id"])
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"container-release evidence error: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
