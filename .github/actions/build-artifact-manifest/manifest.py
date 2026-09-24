#!/usr/bin/env python3
"""Build a relocatable integrity index without reading SDK-owned semantics."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$"
)


class ManifestError(RuntimeError):
    """The requested artifact index would be incomplete or unsafe."""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ManifestError(f"{name} is required")
    return value


def workspace() -> Path:
    return Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve(strict=True)


def _has_symlink_component(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return False
    current = boundary
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            return True
        if not current.exists():
            break
    return False


def workspace_path(value: str, *, kind: str, must_exist: bool = True) -> Path:
    if "\\" in value or "\x00" in value or "\n" in value or "\r" in value:
        raise ManifestError(f"{kind} is not a canonical workspace path")
    root = workspace()
    candidate = Path(value)
    lexical = candidate if candidate.is_absolute() else root / candidate
    lexical = Path(os.path.abspath(lexical))
    try:
        lexical.relative_to(root)
    except ValueError as error:
        raise ManifestError(f"{kind} escapes GITHUB_WORKSPACE") from error
    if _has_symlink_component(lexical, root):
        raise ManifestError(f"{kind} contains a symlink component")
    try:
        resolved = lexical.resolve(strict=must_exist)
    except OSError as error:
        raise ManifestError(f"{kind} is unavailable: {error}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ManifestError(f"{kind} escapes GITHUB_WORKSPACE") from error
    return resolved


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def relative_file(path: Path, artifact_root: Path, *, kind: str) -> str:
    if not path.is_file():
        raise ManifestError(f"{kind} does not exist")
    try:
        relative = path.relative_to(artifact_root)
    except ValueError as error:
        raise ManifestError(f"{kind} is outside artifact-root") from error
    return relative.as_posix()


def canonical_relative(value: str, *, kind: str) -> str:
    if "\\" in value or "\x00" in value or "\n" in value or "\r" in value:
        raise ManifestError(f"{kind} is not a canonical POSIX path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ManifestError(f"{kind} is not a safe relative path")
    normalized = candidate.as_posix()
    if normalized in {"", "."} or normalized != value:
        raise ManifestError(f"{kind} is not a canonical relative path")
    return normalized


def scan_entries(artifact_root: Path, output: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    output_resolved = output.resolve(strict=False)
    for directory, directory_names, file_names in os.walk(
        artifact_root,
        topdown=True,
        followlinks=False,
    ):
        base = Path(directory)
        for name in directory_names:
            candidate = base / name
            if candidate.is_symlink():
                raise ManifestError(
                    f"artifact tree contains symlink directory: "
                    f"{candidate.relative_to(artifact_root).as_posix()}"
                )
        for name in file_names:
            candidate = base / name
            relative = candidate.relative_to(artifact_root).as_posix()
            if candidate.is_symlink() or not candidate.is_file():
                raise ManifestError(
                    f"artifact tree contains non-regular file: {relative}"
                )
            if candidate.resolve(strict=False) == output_resolved:
                continue
            relative = canonical_relative(relative, kind="artifact entry")
            entries.append(
                {
                    "path": relative,
                    "sha256": digest(candidate),
                    "size": candidate.stat().st_size,
                }
            )
    entries.sort(key=lambda entry: str(entry["path"]))
    if not entries:
        raise ManifestError("artifact-root contains no indexable files")
    return entries


def write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    try:
        provider = required("L9_PROVIDER")
        matrix_id = required("L9_MATRIX_ID")
        artifact_name = required("L9_ARTIFACT_NAME")
        repository = required("L9_REPOSITORY")
        repository_revision = required("L9_REPOSITORY_REVISION").lower()
        sdk_revision = required("L9_SDK_REVISION").lower()
        if not SAFE_COMPONENT.fullmatch(provider):
            raise ManifestError("invalid provider")
        if not SAFE_COMPONENT.fullmatch(matrix_id):
            raise ManifestError("invalid matrix-id")
        if not SAFE_ARTIFACT_NAME.fullmatch(artifact_name):
            raise ManifestError("invalid artifact-name")
        if not REPOSITORY.fullmatch(repository):
            raise ManifestError("repository must be an owner/name identifier")
        if not FULL_SHA.fullmatch(repository_revision):
            raise ManifestError("repository-revision must be a full commit SHA")
        if not FULL_SHA.fullmatch(sdk_revision):
            raise ManifestError("sdk-revision must be a full commit SHA")

        artifact_root = workspace_path(
            required("L9_ARTIFACT_ROOT"),
            kind="artifact-root",
        )
        if not artifact_root.is_dir():
            raise ManifestError("artifact-root is not a directory")
        output = workspace_path(
            required("L9_MANIFEST_OUTPUT"),
            kind="index output",
            must_exist=False,
        )
        expected_output = (
            artifact_root / "metadata" / matrix_id / "artifact-index.json"
        ).resolve(strict=False)
        if output != expected_output:
            raise ManifestError(
                "index output must be "
                f"metadata/{matrix_id}/artifact-index.json under artifact-root"
            )
        if output.is_symlink():
            raise ManifestError("index output must not be a symlink")
        if output.exists():
            if not output.is_file():
                raise ManifestError("index output is not a regular file")
            output.unlink()

        bundle = workspace_path(required("L9_BUNDLE"), kind="bundle")
        payload = workspace_path(
            required("L9_AGENT_PAYLOAD"),
            kind="agent payload",
        )
        raw_directory = workspace_path(
            required("L9_RAW_DIRECTORY"),
            kind="raw directory",
        )
        if not raw_directory.is_dir():
            raise ManifestError("raw directory does not exist")
        routing_record = workspace_path(
            required("L9_ROUTING_RECORD"),
            kind="routing record",
        )

        expected_raw = f"raw/{provider}/{matrix_id}"
        expected_bundle = f"l9/{matrix_id}/finding-bundle.json"
        expected_payload = f"l9/{matrix_id}/agent-review-payload.json"
        expected_gate = f"l9/{matrix_id}/gate-result.json"
        expected_routing = f"metadata/{matrix_id}/routing-record.json"
        try:
            raw_relative = raw_directory.relative_to(artifact_root).as_posix()
        except ValueError as error:
            raise ManifestError("raw directory is outside artifact-root") from error
        if raw_relative != expected_raw:
            raise ManifestError(f"raw directory must be {expected_raw}")
        if relative_file(bundle, artifact_root, kind="bundle") != expected_bundle:
            raise ManifestError(f"bundle must be {expected_bundle}")
        if (
            relative_file(payload, artifact_root, kind="agent payload")
            != expected_payload
        ):
            raise ManifestError(f"agent payload must be {expected_payload}")
        if (
            relative_file(routing_record, artifact_root, kind="routing record")
            != expected_routing
        ):
            raise ManifestError(f"routing record must be {expected_routing}")

        entries = scan_entries(artifact_root, output)
        indexed_paths = {str(entry["path"]) for entry in entries}
        raw_prefix = f"{expected_raw}/"
        if not any(path.startswith(raw_prefix) for path in indexed_paths):
            raise ManifestError("raw directory contains no regular files")
        required_paths = {
            expected_bundle,
            expected_gate,
            expected_payload,
            expected_routing,
        }
        missing = sorted(required_paths - indexed_paths)
        if missing:
            raise ManifestError(f"required routed artifacts are not indexed: {missing}")

        routes = {
            "agent_review_payload": expected_payload,
            "finding_bundle": expected_bundle,
            "gate_result": expected_gate,
            "raw_directory": expected_raw,
            "routing_record": expected_routing,
        }
        sarif_path = f"l9/{matrix_id}/results.sarif"
        if sarif_path in indexed_paths:
            routes["sarif"] = sarif_path

        index_path = output.relative_to(artifact_root).as_posix()
        document = {
            "schema": "l9.core-artifact-index/v1",
            "artifact_name": artifact_name,
            "artifact_root": ".",
            "hash_algorithm": "sha256",
            "index_path": index_path,
            "matrix_id": matrix_id,
            "provider": provider,
            "routes": routes,
            "sdk": {
                "integration_contract": "l9.integration-contract/v1",
                "repository": "Quantum-L9/l9-ci-sdk",
                "revision": sdk_revision,
            },
            "subject": {
                "repository": repository,
                "revision": repository_revision,
            },
            "entries": entries,
        }
        write_atomic(output, document)
        return 0
    except (ManifestError, OSError) as error:
        print(f"build-artifact-manifest: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
