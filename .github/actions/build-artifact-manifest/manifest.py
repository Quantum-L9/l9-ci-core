#!/usr/bin/env python3
"""Create deterministic metadata without reconstructing canonical semantics."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ManifestError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ManifestError(f"{name} is required")
    return value


def workspace_path(value: str, *, kind: str) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    candidate = Path(value)
    path = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise ManifestError(f"{kind} escapes GITHUB_WORKSPACE") from error
    return path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def canonical_file(name: str, path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ManifestError(f"{name} does not exist")
    return {"path": path.as_posix(), "sha256": digest(path)}


def main() -> int:
    try:
        provider = required("L9_PROVIDER")
        matrix_id = required("L9_MATRIX_ID")
        sdk_revision = required("L9_SDK_REVISION").lower()
        if not SAFE_COMPONENT.fullmatch(provider):
            raise ManifestError("invalid provider")
        if not SAFE_COMPONENT.fullmatch(matrix_id):
            raise ManifestError("invalid matrix-id")
        if not FULL_SHA.fullmatch(sdk_revision):
            raise ManifestError("sdk-revision must be a full commit SHA")

        bundle = workspace_path(required("L9_BUNDLE"), kind="bundle")
        payload = workspace_path(required("L9_AGENT_PAYLOAD"), kind="agent payload")
        gate_result = workspace_path(required("L9_GATE_RESULT"), kind="gate result")
        raw_directory = workspace_path(
            required("L9_RAW_DIRECTORY"), kind="raw directory"
        )
        output = workspace_path(required("L9_MANIFEST_OUTPUT"), kind="manifest output")
        if not raw_directory.is_dir():
            raise ManifestError("raw directory does not exist")
        raw_files = sorted(path for path in raw_directory.rglob("*") if path.is_file())
        manifest = {
            "schema": "l9.core-artifact-manifest/v2",
            "provider": provider,
            "matrix_id": matrix_id,
            "sdk": {
                "repository": "Quantum-L9/l9-ci-sdk",
                "revision": sdk_revision,
                "integration_contract": "l9.integration-contract/v1",
            },
            "artifacts": {
                "raw": [
                    {"path": path.as_posix(), "sha256": digest(path)}
                    for path in raw_files
                ],
                "canonical": {
                    "bundle": canonical_file("bundle", bundle),
                    "agent_payload": canonical_file("agent payload", payload),
                    "gate_result": canonical_file("gate result", gate_result),
                },
            },
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return 0
    except ManifestError as error:
        print(f"build-artifact-manifest: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
