#!/usr/bin/env python3
"""Verify a consumer repository through immutable Core-owned V2 tooling."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[3]
CORE_TOOLS = CORE_ROOT / "tools"
if str(CORE_TOOLS) not in sys.path:
    sys.path.insert(0, str(CORE_TOOLS))

from l9_repo.__main__ import PHASES, RepositoryWorkflow, WorkflowError  # noqa: E402

MIGRATION_MODE = "migration"
REQUIRED_MODE = "required"


def _write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def _write_result(
    *, present: bool, status: str, contract_version: str, failure_kind: str = ""
) -> None:
    _write_output("present", str(present).lower())
    _write_output("status", status)
    _write_output("contract-version", contract_version)
    _write_output("failure-kind", failure_kind)


def _mode() -> str:
    mode = os.environ.get("L9_REPOSITORY_CONTRACT_MODE", MIGRATION_MODE).strip().lower()
    if mode not in {MIGRATION_MODE, REQUIRED_MODE}:
        raise WorkflowError(f"invalid repository contract mode: {mode!r}")
    return mode


def _execute_v2(workflow: RepositoryWorkflow) -> None:
    workflow.verify_generated()
    for phase in PHASES:
        workflow.execute_phase(phase)


def _execute_v1(workflow: RepositoryWorkflow) -> None:
    for phase in PHASES:
        workflow.execute_legacy_phase(phase)


def main() -> int:
    workspace = Path(os.environ.get("L9_REPOSITORY_WORKSPACE", ".")).resolve()
    contract = workspace / ".l9" / "repo-workflow.json"
    try:
        mode = _mode()
    except Exception as exc:
        _write_result(
            present=False,
            status="contract_failure",
            contract_version="unknown",
            failure_kind="infrastructure",
        )
        print(
            f"repository verification: INFRASTRUCTURE_FAILURE ({exc})", file=sys.stderr
        )
        return 0

    if not contract.is_file():
        if mode == MIGRATION_MODE:
            _write_result(
                present=False, status="legacy_not_applicable", contract_version="absent"
            )
            print(
                "repository verification: LEGACY_NOT_APPLICABLE (migration mode; contract absent)"
            )
        else:
            _write_result(
                present=False,
                status="missing_repository_contract",
                contract_version="absent",
                failure_kind="contract",
            )
            print(
                "repository verification: MISSING_REPOSITORY_CONTRACT", file=sys.stderr
            )
        return 0

    workflow = RepositoryWorkflow(workspace)
    try:
        version = workflow.contract_version()
        if version == "v2":
            _execute_v2(workflow)
        else:
            _execute_v1(workflow)
    except subprocess.CalledProcessError as exc:
        _write_result(
            present=True,
            status="technical_failure",
            contract_version=locals().get("version", "unknown"),
            failure_kind="technical",
        )
        print(f"repository verification: TECHNICAL_FAILURE ({exc})", file=sys.stderr)
        return 0
    except Exception as exc:
        _write_result(
            present=True,
            status="contract_failure",
            contract_version=locals().get("version", "unknown"),
            failure_kind="contract",
        )
        print(f"repository verification: CONTRACT_FAILURE ({exc})", file=sys.stderr)
        return 0

    _write_result(present=True, status="pass", contract_version=version)
    print(f"repository verification: PASS ({version}; " + " -> ".join(PHASES) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
