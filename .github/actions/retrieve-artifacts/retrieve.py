#!/usr/bin/env python3
"""Verify and resolve a downloaded Core artifact set without parsing SDK data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ROUTES = {
    "agent_review_payload",
    "finding_bundle",
    "raw_directory",
    "routing_record",
}
OPTIONAL_ROUTES = {"sarif"}


class RetrievalError(RuntimeError):
    """The downloaded artifact set does not satisfy the Core index contract."""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RetrievalError(f"{name} is required")
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


def workspace_path(value: str, *, kind: str) -> Path:
    root = workspace()
    candidate = Path(value)
    lexical = candidate if candidate.is_absolute() else root / candidate
    lexical = Path(os.path.abspath(lexical))
    try:
        lexical.relative_to(root)
    except ValueError as error:
        raise RetrievalError(f"{kind} escapes GITHUB_WORKSPACE") from error
    if _has_symlink_component(lexical, root):
        raise RetrievalError(f"{kind} contains a symlink component")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise RetrievalError(f"{kind} is unavailable: {error}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RetrievalError(f"{kind} escapes GITHUB_WORKSPACE") from error
    return resolved


def relative_path(value: Any, *, kind: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise RetrievalError(f"{kind} must be a non-empty relative path")
    if "\\" in value or "\x00" in value or "\n" in value or "\r" in value:
        raise RetrievalError(f"{kind} is not a canonical POSIX path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or value.startswith("./") or ".." in candidate.parts:
        raise RetrievalError(f"{kind} is not a safe relative path")
    normalized = candidate.as_posix()
    if normalized in {"", "."} or normalized != value:
        raise RetrievalError(f"{kind} is not a canonical relative path")
    return candidate


def artifact_path(root: Path, relative: PurePosixPath, *, kind: str) -> Path:
    candidate = root.joinpath(*relative.parts)
    if _has_symlink_component(candidate, root):
        raise RetrievalError(f"{kind} contains a symlink component")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise RetrievalError(f"{kind} is unavailable: {error}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RetrievalError(f"{kind} escapes artifact-root") from error
    return resolved


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load_index(index_path: Path) -> dict[str, Any]:
    try:
        document = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetrievalError(f"artifact index is invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise RetrievalError("artifact index must be a JSON object")
    return document


def expected(name: str, pattern: re.Pattern[str]) -> str:
    value = required(name)
    if not pattern.fullmatch(value):
        raise RetrievalError(f"{name} has an invalid value")
    return value


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def verify_entries(
    root: Path,
    index_path: Path,
    entries_value: Any,
) -> dict[str, Path]:
    if not isinstance(entries_value, list) or not entries_value:
        raise RetrievalError("entries must be a non-empty array")
    indexed: dict[str, Path] = {}
    for number, entry in enumerate(entries_value):
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise RetrievalError(f"entry {number} has an invalid shape")
        relative = relative_path(entry["path"], kind=f"entry {number} path")
        relative_text = relative.as_posix()
        if relative_text in indexed:
            raise RetrievalError(f"duplicate index entry: {relative_text}")
        checksum = entry["sha256"]
        size = entry["size"]
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise RetrievalError(f"entry {number} has an invalid sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RetrievalError(f"entry {number} has an invalid size")
        path = artifact_path(root, relative, kind=f"entry {relative_text}")
        if not path.is_file():
            raise RetrievalError(
                f"indexed artifact is not a regular file: {relative_text}"
            )
        if path.stat().st_size != size:
            raise RetrievalError(f"size mismatch for {relative_text}")
        if digest(path) != checksum:
            raise RetrievalError(f"checksum mismatch for {relative_text}")
        indexed[relative_text] = path

    actual: set[str] = set()
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        base = Path(directory)
        for name in directory_names:
            candidate = base / name
            if candidate.is_symlink():
                raise RetrievalError(
                    "artifact tree contains symlink directory: "
                    f"{candidate.relative_to(root).as_posix()}"
                )
        for name in file_names:
            candidate = base / name
            relative_text = candidate.relative_to(root).as_posix()
            if candidate.is_symlink() or not candidate.is_file():
                raise RetrievalError(
                    f"artifact tree contains non-regular file: {relative_text}"
                )
            if candidate.resolve(strict=False) == index_path:
                continue
            actual.add(relative_text)
    indexed_names = set(indexed)
    if actual != indexed_names:
        missing = sorted(indexed_names - actual)
        unindexed = sorted(actual - indexed_names)
        raise RetrievalError(
            f"artifact tree does not match index; missing={missing}, "
            f"unindexed={unindexed}"
        )
    return indexed


def main() -> int:
    try:
        root = workspace_path(required("L9_ARTIFACT_ROOT"), kind="artifact-root")
        if not root.is_dir():
            raise RetrievalError("artifact-root is not a directory")
        matrix_id = expected("L9_MATRIX_ID", SAFE_COMPONENT)
        expected_artifact_name = expected("L9_ARTIFACT_NAME", SAFE_ARTIFACT_NAME)
        expected_repository = expected("L9_REPOSITORY", REPOSITORY)
        expected_revision = expected("L9_REPOSITORY_REVISION", FULL_SHA).lower()
        expected_sdk_revision = expected("L9_SDK_REVISION", FULL_SHA).lower()
        expected_provider = expected("L9_PROVIDER", SAFE_COMPONENT)

        expected_index_relative = PurePosixPath(
            "metadata", matrix_id, "artifact-index.json"
        )
        index_path = artifact_path(
            root,
            expected_index_relative,
            kind="artifact index",
        )
        if not index_path.is_file():
            raise RetrievalError("artifact index is not a regular file")
        index = load_index(index_path)
        required_keys = {
            "schema",
            "artifact_name",
            "artifact_root",
            "hash_algorithm",
            "index_path",
            "matrix_id",
            "provider",
            "routes",
            "sdk",
            "subject",
            "entries",
        }
        if set(index) != required_keys:
            raise RetrievalError(
                "artifact index has unknown or missing top-level fields"
            )
        if index["schema"] != "l9.core-artifact-index/v1":
            raise RetrievalError("unsupported artifact index schema")
        if index["artifact_root"] != "." or index["hash_algorithm"] != "sha256":
            raise RetrievalError("artifact index is not relocatable sha256 metadata")
        if index["index_path"] != expected_index_relative.as_posix():
            raise RetrievalError("artifact index path does not match its location")
        identities = {
            "artifact_name": expected_artifact_name,
            "matrix_id": matrix_id,
            "provider": expected_provider,
        }
        for field, value in identities.items():
            if index[field] != value:
                raise RetrievalError(f"artifact index {field} does not match request")
        if index["subject"] != {
            "repository": expected_repository,
            "revision": expected_revision,
        }:
            raise RetrievalError("artifact index subject does not match request")
        if index["sdk"] != {
            "integration_contract": "l9.integration-contract/v1",
            "repository": "Quantum-L9/l9-ci-sdk",
            "revision": expected_sdk_revision,
        }:
            raise RetrievalError("artifact index SDK identity does not match request")

        indexed = verify_entries(root, index_path, index["entries"])
        routes = index["routes"]
        if not isinstance(routes, dict):
            raise RetrievalError("routes must be an object")
        route_names = set(routes)
        if not REQUIRED_ROUTES.issubset(route_names) or not route_names.issubset(
            REQUIRED_ROUTES | OPTIONAL_ROUTES
        ):
            raise RetrievalError("routes have unknown or missing names")

        expected_routes = {
            "agent_review_payload": f"l9/{matrix_id}/agent-review-payload.json",
            "finding_bundle": f"l9/{matrix_id}/finding-bundle.json",
            "raw_directory": f"raw/{expected_provider}/{matrix_id}",
            "routing_record": f"metadata/{matrix_id}/routing-record.json",
            "sarif": f"l9/{matrix_id}/results.sarif",
        }

        resolved: dict[str, str] = {}
        for name, value in routes.items():
            relative = relative_path(value, kind=f"route {name}")
            route_text = relative.as_posix()
            if route_text != expected_routes[name]:
                raise RetrievalError(f"route {name} is not canonical")
            path = artifact_path(root, relative, kind=f"route {name}")
            if name == "raw_directory":
                if not path.is_dir():
                    raise RetrievalError("raw_directory route is not a directory")
                prefix = f"{route_text}/"
                if not any(candidate.startswith(prefix) for candidate in indexed):
                    raise RetrievalError("raw_directory route has no indexed files")
            else:
                if route_text not in indexed or indexed[route_text] != path:
                    raise RetrievalError(f"route {name} is not an indexed file")
            resolved[name] = path.relative_to(workspace()).as_posix()

        emit("artifact-root", root.relative_to(workspace()).as_posix())
        emit("index", index_path.relative_to(workspace()).as_posix())
        emit("bundle", resolved["finding_bundle"])
        emit("agent-payload", resolved["agent_review_payload"])
        emit("raw-directory", resolved["raw_directory"])
        emit("routing-record", resolved["routing_record"])
        emit("sarif", resolved.get("sarif", ""))
        return 0
    except (OSError, RetrievalError) as error:
        print(f"retrieve-artifacts: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
