#!/usr/bin/env python3
"""Run a consumer repository's canonical execution contract with pinned Core code.

This action is the organization admission point for repository execution and
reads two contract generations from ``.l9/repo-workflow.json``:

``schema_version == 1``
    The existing V1 contract. Parsing, validation, and command execution are
    delegated unchanged to this Core checkout's ``RepositoryWorkflow``.

``schema == "l9.repo-execution/v2"``
    The exact three-field V2 contract (``schema``, ``facade``,
    ``required_phases``). Validation is stdlib-only and fails closed on any
    deviation. Execution is a minimal bridge that runs the declared ``make-v1``
    facade phases ``setup -> validate -> check -> test`` sequentially from the
    supplied workspace, argv-only, and stops at the first failing phase.

Anything else is an invalid contract and records ``status=fail``. A malformed
V2 document never falls through into the V1 parser, and an unknown generation
is never guessed. Contract classification and every execution decision are
made by this immutable Core checkout, never by consumer-vendored code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[3]
CORE_TOOLS = CORE_ROOT / "tools"
if str(CORE_TOOLS) not in sys.path:
    sys.path.insert(0, str(CORE_TOOLS))

from l9_repo.__main__ import RepositoryWorkflow  # noqa: E402

CONTRACT_RELATIVE_PATH = Path(".l9") / "repo-workflow.json"

V1_SCHEMA_VERSION = 1

V2_SCHEMA = "l9.repo-execution/v2"
V2_FACADE = "make-v1"
V2_REQUIRED_PHASES = ("setup", "validate", "check", "test")
V2_ALLOWED_KEYS = frozenset({"schema", "facade", "required_phases"})

CONTRACT_V1 = "v1"
CONTRACT_V2 = "v2"


class ContractError(ValueError):
    """Raised when ``.l9/repo-workflow.json`` is not an admissible contract."""


def _write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def load_contract(path: Path) -> dict[str, object]:
    """Read the contract document once; the root must be a JSON object."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(
            f"unreadable repository contract {path}: {error}"
        ) from error
    if not isinstance(data, dict):
        raise ContractError("repository contract root must be a JSON object")
    return data


def validate_v2_contract(data: dict[str, object]) -> None:
    """Fail closed unless ``data`` is exactly the three-field V2 contract."""

    keys = set(data)
    unknown = sorted(keys - V2_ALLOWED_KEYS)
    if unknown:
        raise ContractError(f"v2 contract has unsupported keys: {', '.join(unknown)}")
    missing = sorted(V2_ALLOWED_KEYS - keys)
    if missing:
        raise ContractError(f"v2 contract missing keys: {', '.join(missing)}")
    if data["schema"] != V2_SCHEMA:
        raise ContractError(f"v2 contract schema must be {V2_SCHEMA!r}")
    if data["facade"] != V2_FACADE:
        raise ContractError(f"v2 contract facade must be {V2_FACADE!r}")
    phases = data["required_phases"]
    if not isinstance(phases, list) or phases != list(V2_REQUIRED_PHASES):
        raise ContractError(
            "v2 contract required_phases must be exactly "
            + json.dumps(list(V2_REQUIRED_PHASES))
        )


def classify_contract(data: dict[str, object]) -> str:
    """Return ``"v1"`` or ``"v2"``; any other generation is rejected."""

    if data.get("schema") == V2_SCHEMA:
        validate_v2_contract(data)
        return CONTRACT_V2
    version = data.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        if version == V1_SCHEMA_VERSION:
            return CONTRACT_V1
    raise ContractError(
        "unrecognized repository contract generation: expected "
        f"schema_version == {V1_SCHEMA_VERSION} or schema == {V2_SCHEMA!r}"
    )


def run_v1(workspace: Path) -> None:
    """Delegate a V1 contract to the unchanged Core repository runtime."""

    workflow = RepositoryWorkflow(workspace)

    # Setup is preparatory. The three authoritative verification phases remain
    # validate -> check -> test. Contract parsing and command execution are
    # performed by this immutable Core checkout, never consumer-vendored
    # tools/l9_repo code.
    workflow.setup()
    workflow.validate()
    workflow.check()
    workflow.test()


def run_v2(workspace: Path) -> None:
    """Execute the declared ``make-v1`` facade phases in order, argv-only.

    The bridge understands nothing about the repository's implementation: it
    does not compile, reconcile, or inspect Makefiles, and it imports no
    consumer Python. ``subprocess.run(check=True)`` raises on the first
    non-zero phase, so later phases never run.
    """

    for phase in V2_REQUIRED_PHASES:
        argv = ["make", phase]
        print("+", " ".join(argv), flush=True)
        subprocess.run(argv, cwd=workspace, check=True)


def main() -> int:
    workspace = Path(os.environ.get("L9_REPOSITORY_WORKSPACE", ".")).resolve()
    contract = workspace / CONTRACT_RELATIVE_PATH
    if not contract.is_file():
        _write_output("present", "false")
        _write_output("status", "not_applicable")
        print("repository verification: NOT_APPLICABLE (.l9/repo-workflow.json absent)")
        return 0

    _write_output("present", "true")
    generation = "unknown"
    try:
        generation = classify_contract(load_contract(contract))
        if generation == CONTRACT_V2:
            run_v2(workspace)
        else:
            run_v1(workspace)
    except Exception as exc:
        # Expected repository-verification failures become typed evidence so the
        # existing SDK/Semgrep analysis can still finish. The workflow's final
        # enforcement step fails blocking profiles on any non-pass typed result.
        _write_output("status", "fail")
        print(f"repository verification: FAIL ({generation}; {exc})", file=sys.stderr)
        return 0

    _write_output("status", "pass")
    print(f"repository verification: PASS ({generation}; validate + check + test)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
