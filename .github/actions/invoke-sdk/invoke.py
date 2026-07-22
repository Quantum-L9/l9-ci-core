#!/usr/bin/env python3
"""Safe public-CLI adapter for l9-ci-sdk."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

OPERATIONS = {
    "semgrep-normalize",
    "semgrep-run",
    "bundle-validate",
    "bundle-project-agent-payload",
    "compatibility-check",
    "gate-evaluate",
}
BOOLEAN_VALUES = {"true", "false"}
DIRTY_VALUES = {"true", "false", "unset"}
GATE_EXIT_BY_STATUS = {
    "pass": 0,
    "fail": 1,
    "invalid": 5,
    "incomplete": 6,
}
MAX_PROVIDER_ARGUMENTS = 128
MAX_PROVIDER_ARGUMENT_LENGTH = 4096


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
    path = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
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


def positive_int(value: str, label: str) -> int:
    try:
        parsed = int(require(value, label))
    except ValueError as error:
        raise InvocationError(f"{label} must be a positive integer") from error
    if parsed <= 0:
        raise InvocationError(f"{label} must be a positive integer")
    return parsed


def provider_arguments() -> tuple[str, ...]:
    raw = env("L9_ARGUMENTS_JSON", "[]")
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InvocationError("arguments-json must be valid JSON") from error
    if not isinstance(payload, list):
        raise InvocationError("arguments-json must contain a JSON array")
    if len(payload) > MAX_PROVIDER_ARGUMENTS:
        raise InvocationError(
            f"arguments-json must contain at most {MAX_PROVIDER_ARGUMENTS} items"
        )
    arguments: list[str] = []
    for index, value in enumerate(payload):
        if not isinstance(value, str):
            raise InvocationError(f"arguments-json item {index} must be a string")
        if "\x00" in value:
            raise InvocationError(f"arguments-json item {index} contains NUL")
        if len(value) > MAX_PROVIDER_ARGUMENT_LENGTH:
            raise InvocationError(
                f"arguments-json item {index} exceeds "
                f"{MAX_PROVIDER_ARGUMENT_LENGTH} characters"
            )
        arguments.append(value)
    return tuple(arguments)


def add_common_semgrep_options(command: list[str], *, require_version: bool) -> None:
    provider_version = env("L9_PROVIDER_VERSION")
    if require_version:
        provider_version = require(provider_version, "provider-version")
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
    generated_at = require(env("L9_GENERATED_AT"), "generated-at")
    command.extend(["--generated-at", generated_at])
    revision = env("L9_REVISION")
    if revision:
        command.extend(["--revision", revision])
    strict = parse_boolean(env("L9_STRICT", "true"), "strict")
    required_provider = parse_boolean(env("L9_REQUIRED", "true"), "required")
    dirty = env("L9_DIRTY", "unset")
    if dirty not in DIRTY_VALUES:
        raise InvocationError("dirty must be true, false, or unset")
    if strict:
        command.append("--strict")
    if required_provider:
        command.append("--required")
    if dirty == "true":
        command.append("--dirty")
    elif dirty == "false":
        command.append("--no-dirty")


def build_command(executable: Path) -> list[str]:
    operation = require(env("L9_OPERATION"), "operation")
    if operation not in OPERATIONS:
        raise InvocationError(f"unsupported operation: {operation}")

    input_value = env("L9_INPUT")
    output_value = env("L9_OUTPUT")

    if operation in {"semgrep-normalize", "semgrep-run"}:
        report_path = resolve_workspace_path(
            input_value,
            "input",
            must_exist=operation == "semgrep-normalize",
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
        report_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if operation == "semgrep-normalize":
            command = [
                str(executable),
                "semgrep",
                "normalize",
                "--input",
                str(report_path),
                "--output",
                str(output_path),
                "--root",
                str(root),
                "--snapshot-id",
                snapshot_id,
            ]
            add_common_semgrep_options(command, require_version=True)
            return command

        command = [
            str(executable),
            "semgrep",
            "run",
            "--report",
            str(report_path),
            "--output",
            str(output_path),
            "--root",
            str(root),
            "--snapshot-id",
            snapshot_id,
            "--timeout-seconds",
            str(positive_int(env("L9_TIMEOUT_SECONDS", "300"), "timeout-seconds")),
            "--output-size-limit-bytes",
            str(
                positive_int(
                    env("L9_OUTPUT_SIZE_LIMIT_BYTES", "50000000"),
                    "output-size-limit-bytes",
                )
            ),
        ]
        for argument in provider_arguments():
            command.append(f"--execution-arg={argument}")
        add_common_semgrep_options(command, require_version=False)
        return command

    bundle = resolve_workspace_path(input_value, "input", must_exist=True)
    if operation == "bundle-validate":
        return [str(executable), "bundle", "validate", str(bundle)]

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
        if parse_boolean(env("L9_STRICT", "true"), "strict"):
            command.append("--strict")
        return command

    if operation == "gate-evaluate":
        output_path = resolve_workspace_path(
            output_value,
            "output",
            must_exist=False,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        strict = parse_boolean(env("L9_STRICT", "true"), "strict")
        return [
            str(executable),
            "gate",
            "evaluate",
            "--bundle",
            str(bundle),
            "--output",
            str(output_path),
            "--strict-unresolved" if strict else "--no-strict-unresolved",
        ]

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


def gate_status_for_exit(output_path: Path, exit_code: int) -> str | None:
    if not output_path.is_file():
        return None
    try:
        document = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("schema") != "l9.gate-result/v1":
        return None
    if document.get("schema_version") != "1.0.0":
        return None
    status = document.get("status")
    if not isinstance(status, str):
        return None
    expected_exit = GATE_EXIT_BY_STATUS.get(status)
    if expected_exit != exit_code:
        return None
    return status


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
        operation = require(env("L9_OPERATION"), "operation")
        command = build_command(executable)
    except InvocationError as error:
        print(f"invoke-sdk: {error}", file=sys.stderr)
        return 2

    printable = " ".join(command)
    print(f"Executing SDK command: {printable}")
    result = subprocess.run(command, check=False)
    emit_output("exit-code", str(result.returncode))
    emit_output("command", printable)

    if operation == "gate-evaluate":
        try:
            output_path = resolve_workspace_path(
                env("L9_OUTPUT"),
                "output",
                must_exist=False,
            )
        except InvocationError:
            return result.returncode
        status = gate_status_for_exit(output_path, result.returncode)
        if status is not None:
            emit_output("gate-status", status)
            # PASS/FAIL/INCOMPLETE/INVALID are semantic outcomes carried by the
            # canonical gate-result artifact. The action succeeds so Core can
            # route and publish that artifact without converting a gate decision
            # into an infrastructure failure.
            return 0

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
