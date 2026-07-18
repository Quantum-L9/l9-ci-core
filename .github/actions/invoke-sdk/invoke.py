#!/usr/bin/env python3
"""Safe public-CLI adapter for l9-ci-sdk."""

from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

OPERATIONS = {
    "semgrep-normalize",
    "bundle-validate",
    "bundle-project-agent-payload",
    "compatibility-check",
}
BOOLEAN_VALUES = {"true", "false"}
DIRTY_VALUES = {"true", "false", "unset"}


class InvocationError(RuntimeError):
    pass


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def require(value: str, label: str) -> str:
    if not value:
        raise InvocationError(f"{label} is required")
    return value


def validate_executable(value: str) -> Path:
    path = Path(require(value, "executable")).resolve()
    if not path.is_file():
        raise InvocationError(f"SDK executable does not exist: {path}")
    if not os.access(path, os.X_OK):
        raise InvocationError(f"SDK executable is not executable: {path}")
    return path


def resolve_workspace_path(
    value: str,
    label: str,
    *,
    must_exist: bool,
) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    candidate = Path(require(value, label))
    path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace / candidate).resolve()
    )
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise InvocationError(f"{label} must remain inside GITHUB_WORKSPACE") from error
    if must_exist and not path.exists():
        raise InvocationError(f"{label} does not exist: {path}")
    return path


def add_optional_path(
    command: list[str],
    flag: str,
    value: str,
    label: str,
) -> None:
    if value:
        command.extend(
            [
                flag,
                str(resolve_workspace_path(value, label, must_exist=True)),
            ]
        )


def parse_boolean(value: str, label: str) -> bool:
    if value not in BOOLEAN_VALUES:
        raise InvocationError(f"{label} must be true or false")
    return value == "true"


def build_command(executable: Path) -> list[str]:
    operation = require(env("L9_OPERATION"), "operation")
    if operation not in OPERATIONS:
        raise InvocationError(f"unsupported operation: {operation}")
    input_value = env("L9_INPUT")
    output_value = env("L9_OUTPUT")
    strict = parse_boolean(env("L9_STRICT", "true"), "strict")
    required = parse_boolean(env("L9_REQUIRED", "true"), "required")
    dirty = env("L9_DIRTY", "unset")
    if dirty not in DIRTY_VALUES:
        raise InvocationError("dirty must be true, false, or unset")
    if operation == "semgrep-normalize":
        input_path = resolve_workspace_path(
            input_value,
            "input",
            must_exist=True,
        )
        output_path = resolve_workspace_path(
            output_value,
            "output",
            must_exist=False,
        )
        root = resolve_workspace_path(
            env("L9_ROOT", "."),
            "root",
            must_exist=True,
        )
        snapshot_id = require(env("L9_SNAPSHOT_ID"), "snapshot-id")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(executable),
            "semgrep",
            "normalize",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--root",
            str(root),
            "--snapshot-id",
            snapshot_id,
        ]
        provider_version = env("L9_PROVIDER_VERSION")
        if provider_version:
            command.extend(["--provider-version", provider_version])
        add_optional_path(
            command,
            "--identity-map",
            env("L9_IDENTITY_MAP"),
            "identity-map",
        )
        add_optional_path(
            command,
            "--policy",
            env("L9_POLICY"),
            "policy",
        )
        generated_at = env("L9_GENERATED_AT")
        if generated_at:
            command.extend(["--generated-at", generated_at])
        revision = env("L9_REVISION")
        if revision:
            command.extend(["--revision", revision])
        if strict:
            command.append("--strict")
        if required:
            command.append("--required")
        if dirty == "true":
            command.append("--dirty")
        elif dirty == "false":
            command.append("--no-dirty")
        return command
    bundle = resolve_workspace_path(
        input_value,
        "input",
        must_exist=True,
    )
    if operation == "bundle-validate":
        return [
            str(executable),
            "bundle",
            "validate",
            str(bundle),
        ]
    if operation == "bundle-project-agent-payload":
        output_path = resolve_workspace_path(
            output_value,
            "output",
            must_exist=False,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(executable),
            "bundle",
            "project-agent-payload",
            "--input",
            str(bundle),
            "--output",
            str(output_path),
        ]
        if strict:
            command.append("--strict")
        return command
    command = [
        str(executable),
        "compatibility",
        "check",
        "--bundle",
        str(bundle),
    ]
    minimum = env("L9_MINIMUM_SDK_VERSION")
    if minimum:
        command.extend(["--minimum-SDK-version", minimum])
    return command


def emit_output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    try:
        executable = validate_executable(env("L9_EXECUTABLE"))
        command = build_command(executable)
    except InvocationError as error:
        print(f"invoke-sdk: {error}", file=sys.stderr)
        return 2
    printable = " ".join(command)
    print(f"Executing SDK command: {printable}")
    result = subprocess.run(command, check=False)
    emit_output("exit-code", str(result.returncode))
    emit_output("command", printable)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
