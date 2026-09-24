#!/usr/bin/env python3
"""Local leaf adapter for SDK bundle validation and compatibility checking."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class ValidationError(RuntimeError):
    pass


def require_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValidationError(f"{name} must not be empty")
    return value


def validate_executable(value: str) -> Path:
    supplied = Path(value)
    if not supplied.is_absolute():
        raise ValidationError("executable must be an absolute path")
    executable = supplied.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValidationError(f"SDK executable is not executable: {executable}")
    return executable


def validate_bundle(value: str) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    supplied = Path(value)
    bundle = (
        supplied.resolve()
        if supplied.is_absolute()
        else (workspace / supplied).resolve()
    )
    try:
        bundle.relative_to(workspace)
    except ValueError as error:
        raise ValidationError("bundle must remain inside GITHUB_WORKSPACE") from error
    if not bundle.is_file():
        raise ValidationError(f"bundle does not exist: {bundle}")
    return bundle


def commands(executable: Path, bundle: Path, minimum: str) -> list[list[str]]:
    result = [
        [str(executable), "bundle", "validate", str(bundle)],
        [str(executable), "compatibility", "check", "--bundle", str(bundle)],
    ]
    if minimum:
        result[1].extend(["--minimum-SDK-version", minimum])
    return result


def main() -> int:
    try:
        executable = validate_executable(require_environment("L9_EXECUTABLE"))
        bundle = validate_bundle(require_environment("L9_BUNDLE"))
        argv = commands(
            executable,
            bundle,
            os.environ.get("L9_MINIMUM_SDK_VERSION", "").strip(),
        )
    except ValidationError as error:
        print(f"validate-bundle: {error}", file=sys.stderr)
        return 2
    for command in argv:
        print(f"Executing SDK command: {' '.join(command)}")
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
