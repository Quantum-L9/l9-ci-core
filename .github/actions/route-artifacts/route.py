#!/usr/bin/env python3
"""Route artifacts without mutating SDK-owned canonical content."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RoutingError(RuntimeError):
    """The requested artifact route is unsafe or ambiguous."""


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RoutingError(f"{name} is required")
    return value


def component(name: str, value: str) -> str:
    if not SAFE_COMPONENT.fullmatch(value):
        raise RoutingError(f"{name} must match {SAFE_COMPONENT.pattern!r}")
    return value


def workspace() -> Path:
    return Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve(strict=True)


def _has_symlink_component(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return False
    current = boundary
    for path_component in relative.parts:
        current = current / path_component
        if current.is_symlink():
            return True
        if not current.exists():
            break
    return False


def workspace_path(value: str, *, must_exist: bool) -> Path:
    root = workspace()
    candidate = Path(value)
    lexical = candidate if candidate.is_absolute() else root / candidate
    lexical = Path(os.path.abspath(lexical))
    try:
        lexical.relative_to(root)
    except ValueError as error:
        raise RoutingError("artifact path escapes GITHUB_WORKSPACE") from error
    if _has_symlink_component(lexical, root):
        raise RoutingError("artifact path contains a symlink component")
    try:
        path = lexical.resolve(strict=must_exist)
    except OSError as error:
        raise RoutingError(f"artifact path is unavailable: {error}") from error
    try:
        path.relative_to(root)
    except ValueError as error:
        raise RoutingError("artifact path escapes GITHUB_WORKSPACE") from error
    if must_exist and not path.is_file():
        raise RoutingError(f"artifact does not exist: {path}")
    return path


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def copy_exact(source: Path, destination: Path, *, boundary: Path) -> None:
    if _has_symlink_component(destination, boundary):
        raise RoutingError(f"artifact destination contains a symlink: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise RoutingError(f"artifact destination is a symlink: {destination}")
    if destination.exists():
        if not destination.is_file():
            raise RoutingError(f"artifact destination is not a file: {destination}")
        if source.resolve() == destination.resolve():
            return
        raise RoutingError(f"artifact destination already exists: {destination}")
    shutil.copyfile(source, destination)
    if sha256(source) != sha256(destination):
        raise RoutingError(f"byte-preserving copy verification failed: {source}")


def write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RoutingError(f"routing record is a symlink: {path}")
    if path.exists():
        raise RoutingError(f"routing record already exists: {path}")
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


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    try:
        provider = component("provider", env("L9_PROVIDER"))
        matrix_id = component("matrix-id", env("L9_MATRIX_ID"))
        workspace_root = workspace()
        raw_source = workspace_path(env("L9_RAW_REPORT"), must_exist=True)
        bundle_source = workspace_path(env("L9_BUNDLE"), must_exist=True)
        payload_source = workspace_path(
            env("L9_AGENT_PAYLOAD"),
            must_exist=True,
        )
        destination_root = workspace_path(
            env("L9_DESTINATION_ROOT"),
            must_exist=False,
        )
        if destination_root == workspace_root:
            raise RoutingError("destination root must not be GITHUB_WORKSPACE")
        if destination_root.exists() and not destination_root.is_dir():
            raise RoutingError("destination root is not a directory")

        # The projected SARIF is an optional, SDK-produced derived artifact. Core
        # routes it byte-for-byte alongside the canonical bundle and never
        # generates or edits SARIF itself.
        sarif_value = os.environ.get("L9_SARIF", "").strip()
        sarif_source = (
            workspace_path(sarif_value, must_exist=True) if sarif_value else None
        )
        raw_directory = destination_root / "raw" / provider / matrix_id
        canonical_directory = destination_root / "l9" / matrix_id
        raw_destination = raw_directory / raw_source.name
        bundle_destination = canonical_directory / "finding-bundle.json"
        payload_destination = canonical_directory / "agent-review-payload.json"
        sarif_destination = canonical_directory / "results.sarif"
        routed = [raw_destination, bundle_destination, payload_destination]
        if sarif_source is not None:
            routed.append(sarif_destination)
        if len({destination.resolve() for destination in routed}) != len(routed):
            raise RoutingError("artifact destinations collide")

        copy_exact(raw_source, raw_destination, boundary=destination_root)
        copy_exact(bundle_source, bundle_destination, boundary=destination_root)
        copy_exact(payload_source, payload_destination, boundary=destination_root)
        if sarif_source is not None:
            copy_exact(sarif_source, sarif_destination, boundary=destination_root)

        artifacts_record: dict[str, dict[str, str | int]] = {
            "raw": {
                "path": raw_destination.relative_to(destination_root).as_posix(),
                "sha256": sha256(raw_destination),
                "size": raw_destination.stat().st_size,
            },
            "bundle": {
                "path": bundle_destination.relative_to(destination_root).as_posix(),
                "sha256": sha256(bundle_destination),
                "size": bundle_destination.stat().st_size,
            },
            "agent_payload": {
                "path": payload_destination.relative_to(destination_root).as_posix(),
                "sha256": sha256(payload_destination),
                "size": payload_destination.stat().st_size,
            },
        }
        if sarif_source is not None:
            artifacts_record["sarif"] = {
                "path": sarif_destination.relative_to(destination_root).as_posix(),
                "sha256": sha256(sarif_destination),
                "size": sarif_destination.stat().st_size,
            }
        routing_record = {
            "schema": "l9.core-routing-record/v2",
            "artifact_root": ".",
            "provider": provider,
            "matrix_id": matrix_id,
            "artifacts": artifacts_record,
        }
        routing_path = destination_root / "metadata" / matrix_id / "routing-record.json"
        if _has_symlink_component(routing_path, destination_root):
            raise RoutingError("routing record contains a symlink component")
        write_atomic(routing_path, routing_record)

        emit("raw-directory", raw_directory.relative_to(workspace_root).as_posix())
        emit("bundle", bundle_destination.relative_to(workspace_root).as_posix())
        emit(
            "agent-payload",
            payload_destination.relative_to(workspace_root).as_posix(),
        )
        emit("routing-record", routing_path.relative_to(workspace_root).as_posix())
        emit(
            "sarif",
            sarif_destination.relative_to(workspace_root).as_posix()
            if sarif_source is not None
            else "",
        )
        return 0
    except (OSError, RoutingError) as error:
        print(f"route-artifacts: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
