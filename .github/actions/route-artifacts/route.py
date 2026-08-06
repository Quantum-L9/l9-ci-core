#!/usr/bin/env python3
"""Route artifacts without mutating SDK-owned canonical content."""

from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RoutingError(RuntimeError):
    pass


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RoutingError(f"{name} is required")
    return value


def component(name: str, value: str) -> str:
    if not SAFE_COMPONENT.fullmatch(value):
        raise RoutingError(f"{name} must match {SAFE_COMPONENT.pattern!r}")
    return value


def workspace_path(value: str, *, must_exist: bool) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    candidate = Path(value)
    path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace / candidate).resolve()
    )
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise RoutingError("artifact path escapes GITHUB_WORKSPACE") from error
    if must_exist and not path.is_file():
        raise RoutingError(f"artifact does not exist: {path}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_exact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        # The caller already wrote the artifact at its routed destination
        # (e.g. a consumer whose provider report lives under destination-root).
        # Copying a file onto itself raises SameFileError; it is already routed.
        return
    shutil.copyfile(source, destination)
    if sha256(source) != sha256(destination):
        raise RoutingError(f"byte-preserving copy verification failed: {source}")


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
        copy_exact(raw_source, raw_destination)
        copy_exact(bundle_source, bundle_destination)
        copy_exact(payload_source, payload_destination)
        if sarif_source is not None:
            copy_exact(sarif_source, sarif_destination)
        metadata_directory = destination_root / "metadata" / matrix_id
        metadata_directory.mkdir(parents=True, exist_ok=True)
        artifacts_record: dict[str, dict[str, str]] = {
            "raw": {
                "path": raw_destination.as_posix(),
                "sha256": sha256(raw_destination),
            },
            "bundle": {
                "path": bundle_destination.as_posix(),
                "sha256": sha256(bundle_destination),
            },
            "agent_payload": {
                "path": payload_destination.as_posix(),
                "sha256": sha256(payload_destination),
            },
        }
        if sarif_source is not None:
            artifacts_record["sarif"] = {
                "path": sarif_destination.as_posix(),
                "sha256": sha256(sarif_destination),
            }
        routing_record = {
            "schema": "l9.core-routing-record/v1",
            "provider": provider,
            "matrix_id": matrix_id,
            "artifacts": artifacts_record,
        }
        routing_path = metadata_directory / "routing-record.json"
        routing_path.write_text(
            json.dumps(
                routing_record,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        emit("raw-directory", str(raw_directory))
        emit("bundle", str(bundle_destination))
        emit("agent-payload", str(payload_destination))
        emit("sarif", str(sarif_destination) if sarif_source is not None else "")
        return 0
    except RoutingError as error:
        print(f"route-artifacts: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
