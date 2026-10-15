#!/usr/bin/env python3
"""Create canonical Core artifact transport metadata without reading SDK data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
POSITIVE_ID = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HandoffError(RuntimeError):
    """The requested handoff descriptor would be malformed or unsafe."""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise HandoffError(f"{name} is required")
    return value


def checked(name: str, pattern: re.Pattern[str]) -> str:
    value = required(name)
    if not pattern.fullmatch(value):
        raise HandoffError(f"{name} has an invalid value")
    return value


def full_sha(name: str) -> str:
    value = required(name).lower()
    if not FULL_SHA.fullmatch(value):
        raise HandoffError(f"{name} must be a full commit SHA")
    return value


def archive_digest() -> str:
    value = required("L9_ARTIFACT_DIGEST").lower()
    if value.startswith("sha256:"):
        value = value.removeprefix("sha256:")
    if not SHA256.fullmatch(value):
        raise HandoffError("L9_ARTIFACT_DIGEST must be a SHA-256 digest")
    return value


def workspace() -> Path:
    try:
        return Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve(strict=True)
    except OSError as error:
        raise HandoffError(f"GITHUB_WORKSPACE is unavailable: {error}") from error


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


def new_destination(value: str) -> tuple[Path, Path]:
    if "\\" in value or "\x00" in value or "\n" in value or "\r" in value:
        raise HandoffError("descriptor destination is not a canonical workspace path")
    root = workspace()
    relative_posix = PurePosixPath(value)
    if (
        relative_posix.is_absolute()
        or ".." in relative_posix.parts
        or value.startswith("./")
        or relative_posix.as_posix() != value
    ):
        raise HandoffError("descriptor destination is not a safe relative path")
    relative = Path(*relative_posix.parts)
    if relative == Path("."):
        raise HandoffError(
            "descriptor destination must be a file below GITHUB_WORKSPACE"
        )
    lexical = root / relative
    if _has_symlink_component(lexical, root):
        raise HandoffError("descriptor destination contains a symlink component")
    if lexical.exists() or lexical.is_symlink():
        raise HandoffError("descriptor destination is stale")
    try:
        lexical.parent.mkdir(parents=True, exist_ok=True)
        parent = lexical.parent.resolve(strict=True)
    except OSError as error:
        raise HandoffError(f"descriptor destination is unavailable: {error}") from error
    try:
        parent.relative_to(root)
    except ValueError as error:
        raise HandoffError("descriptor destination escapes GITHUB_WORKSPACE") from error
    if _has_symlink_component(lexical, root):
        raise HandoffError("descriptor destination contains a symlink component")
    if not parent.is_dir():
        raise HandoffError("descriptor destination parent is not a directory")
    return lexical, relative


def canonical_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def write_new(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise HandoffError(
            f"could not create descriptor destination: {error}"
        ) from error


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    try:
        artifact_id_text = checked("L9_ARTIFACT_ID", POSITIVE_ID)
        run_id_text = checked("L9_PRODUCER_RUN_ID", POSITIVE_ID)
        document = {
            "schema": "l9.core-artifact-handoff/v1",
            "producer": {
                "repository": checked("L9_PRODUCER_REPOSITORY", REPOSITORY),
                "run_id": int(run_id_text),
                "head_sha": full_sha("L9_WORKFLOW_HEAD_SHA"),
            },
            "artifact": {
                "id": int(artifact_id_text),
                "name": checked("L9_ARTIFACT_NAME", SAFE_ARTIFACT_NAME),
                "archive_digest": {
                    "algorithm": "sha256",
                    "value": archive_digest(),
                },
            },
            "subject": {
                "repository": checked("L9_REPOSITORY", REPOSITORY),
                "revision": full_sha("L9_REPOSITORY_REVISION"),
            },
            "provider": checked("L9_PROVIDER", SAFE_COMPONENT),
            "matrix_id": checked("L9_MATRIX_ID", SAFE_COMPONENT),
            "sdk": {
                "integration_contract": "l9.integration-contract/v1",
                "repository": "Quantum-L9/l9-ci-sdk",
                "revision": full_sha("L9_SDK_REVISION"),
            },
        }
        destination, relative = new_destination(required("L9_HANDOFF_OUTPUT"))
        content = canonical_bytes(document)
        write_new(destination, content)
        emit("descriptor", content.decode("utf-8").rstrip("\n"))
        emit("descriptor-path", relative.as_posix())
        emit("descriptor-digest", hashlib.sha256(content).hexdigest())
        return 0
    except (HandoffError, OSError) as error:
        print(f"create-artifact-handoff: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
