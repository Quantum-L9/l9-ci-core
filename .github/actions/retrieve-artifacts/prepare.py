#!/usr/bin/env python3
"""Select retrieval mode and require a safe, empty download destination."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
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


class PreparationError(RuntimeError):
    """The requested download destination is unsafe."""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise PreparationError(f"{name} is required")
    return value


def checked(name: str, pattern: re.Pattern[str]) -> str:
    value = required(name)
    if not pattern.fullmatch(value):
        raise PreparationError(f"{name} has an invalid value")
    return value


def descriptor_string(value: Any, *, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PreparationError(f"descriptor {field} has an invalid value")
    return value


def descriptor_id(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PreparationError(f"descriptor {field} has an invalid value")
    if not POSITIVE_ID.fullmatch(str(value)):
        raise PreparationError(f"descriptor {field} is outside the supported range")
    return value


def descriptor_digest(value: Any) -> str:
    if not isinstance(value, dict) or set(value) != {"algorithm", "value"}:
        raise PreparationError(
            "descriptor artifact archive_digest has an invalid shape"
        )
    if value["algorithm"] != "sha256":
        raise PreparationError("descriptor artifact archive_digest is not SHA-256")
    return descriptor_string(
        value["value"],
        field="artifact.archive_digest.value",
        pattern=SHA256,
    )


def parse_descriptor(value: str) -> dict[str, str]:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"invalid JSON constant: {constant}")

    try:
        document = json.loads(value, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise PreparationError(f"descriptor is invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise PreparationError("descriptor must be a JSON object")
    if set(document) != {
        "schema",
        "producer",
        "artifact",
        "subject",
        "provider",
        "matrix_id",
        "sdk",
    }:
        raise PreparationError("descriptor has unknown or missing top-level fields")
    if document["schema"] != "l9.core-artifact-handoff/v1":
        raise PreparationError("unsupported descriptor schema")

    producer = document["producer"]
    artifact = document["artifact"]
    subject = document["subject"]
    sdk = document["sdk"]
    if not isinstance(producer, dict) or set(producer) != {"repository", "run_id"}:
        raise PreparationError("descriptor producer has an invalid shape")
    if not isinstance(artifact, dict) or set(artifact) != {
        "id",
        "name",
        "archive_digest",
    }:
        raise PreparationError("descriptor artifact has an invalid shape")
    if not isinstance(subject, dict) or set(subject) != {"repository", "revision"}:
        raise PreparationError("descriptor subject has an invalid shape")
    if not isinstance(sdk, dict) or set(sdk) != {
        "integration_contract",
        "repository",
        "revision",
    }:
        raise PreparationError("descriptor SDK identity has an invalid shape")
    if sdk["integration_contract"] != "l9.integration-contract/v1":
        raise PreparationError("descriptor SDK integration contract is unsupported")
    if sdk["repository"] != "Quantum-L9/l9-ci-sdk":
        raise PreparationError("descriptor SDK repository is unsupported")

    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    if value != canonical:
        raise PreparationError("descriptor is not canonical single-line JSON")

    return {
        "mode": "descriptor",
        "artifact-id": str(descriptor_id(artifact["id"], field="artifact.id")),
        "artifact-name": descriptor_string(
            artifact["name"], field="artifact.name", pattern=SAFE_ARTIFACT_NAME
        ),
        "archive-digest": descriptor_digest(artifact["archive_digest"]),
        "source-repository": descriptor_string(
            producer["repository"], field="producer.repository", pattern=REPOSITORY
        ),
        "source-run-id": str(
            descriptor_id(producer["run_id"], field="producer.run_id")
        ),
        "repository": descriptor_string(
            subject["repository"], field="subject.repository", pattern=REPOSITORY
        ),
        "repository-revision": descriptor_string(
            subject["revision"], field="subject.revision", pattern=FULL_SHA
        ),
        "provider": descriptor_string(
            document["provider"], field="provider", pattern=SAFE_COMPONENT
        ),
        "matrix-id": descriptor_string(
            document["matrix_id"], field="matrix_id", pattern=SAFE_COMPONENT
        ),
        "sdk-revision": descriptor_string(
            sdk["revision"], field="sdk.revision", pattern=FULL_SHA
        ),
    }


def mode_outputs() -> dict[str, str]:
    artifact_name = os.environ.get("L9_ARTIFACT_NAME", "").strip()
    descriptor = os.environ.get("L9_HANDOFF_DESCRIPTOR", "")
    if bool(artifact_name) == bool(descriptor.strip()):
        raise PreparationError(
            "exactly one of artifact-name or handoff-descriptor is required"
        )
    if descriptor.strip():
        required("L9_TOKEN")
        return parse_descriptor(descriptor)
    return {
        "mode": "current-run",
        "artifact-id": "",
        "artifact-name": checked("L9_ARTIFACT_NAME", SAFE_ARTIFACT_NAME),
        "archive-digest": "",
        "source-repository": "",
        "source-run-id": "",
        "repository": checked("L9_REPOSITORY", REPOSITORY),
        "repository-revision": checked("L9_REPOSITORY_REVISION", FULL_SHA),
        "provider": checked("L9_PROVIDER", SAFE_COMPONENT),
        "matrix-id": checked("L9_MATRIX_ID", SAFE_COMPONENT),
        "sdk-revision": checked("L9_SDK_REVISION", FULL_SHA),
    }


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def has_symlink_component(path: Path, boundary: Path) -> bool:
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


def main() -> int:
    try:
        outputs = mode_outputs()
        value = required("L9_DESTINATION")
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve(
            strict=True
        )
        candidate = Path(value)
        lexical = candidate if candidate.is_absolute() else workspace / candidate
        lexical = Path(os.path.abspath(lexical))
        try:
            relative = lexical.relative_to(workspace)
        except ValueError as error:
            raise PreparationError("destination escapes GITHUB_WORKSPACE") from error
        if relative == Path("."):
            raise PreparationError("destination must not be GITHUB_WORKSPACE")
        if has_symlink_component(lexical, workspace):
            raise PreparationError("destination contains a symlink component")
        if lexical.exists():
            if not lexical.is_dir():
                raise PreparationError("destination is not a directory")
            try:
                next(lexical.iterdir())
            except StopIteration:
                pass
            else:
                raise PreparationError("destination must be empty before download")
        else:
            lexical.mkdir(parents=True)
        resolved = lexical.resolve(strict=True)
        try:
            resolved.relative_to(workspace)
        except ValueError as error:
            raise PreparationError("destination escapes GITHUB_WORKSPACE") from error
        for name, output in outputs.items():
            emit(name, output)
        return 0
    except (OSError, PreparationError) as error:
        print(f"retrieve-artifacts: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
