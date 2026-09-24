#!/usr/bin/env python3
"""Verify a consumer repository through immutable Core-owned V2 tooling.

Every run ends in exactly one typed result:

    V2_PASS            V2 contract; generated facade verified; every phase passed
    V1_COMPAT          valid V1 contract executed through the bounded migration
                       path (migration mode only)
    CONTRACT_MISSING   no contract (tolerated in migration mode, fatal in required)
    CONTRACT_INVALID   malformed contract, drifted facade, broken Repo.mk, or a
                       V1 contract in required mode
    TECHNICAL_FAILURE  a repository phase ran and failed

The V1 path is migration-only: remove it, and the `migration` mode, once every
governed repository declares V2.
"""

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

V2_PASS = "V2_PASS"
V1_COMPAT = "V1_COMPAT"
CONTRACT_MISSING = "CONTRACT_MISSING"
CONTRACT_INVALID = "CONTRACT_INVALID"
TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


def _write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def _write_result(
    *,
    result: str,
    present: bool,
    status: str,
    contract_version: str,
    failure_kind: str = "",
) -> None:
    _write_output("result", result)
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
            result=CONTRACT_INVALID,
            present=False,
            status="contract_failure",
            contract_version="unknown",
            failure_kind="infrastructure",
        )
        print(
            f"repository verification: INFRASTRUCTURE_FAILURE ({exc})", file=sys.stderr
        )
        return 0
    _write_output("contract-mode", mode)

    if not contract.is_file():
        if mode == MIGRATION_MODE:
            _write_result(
                result=CONTRACT_MISSING,
                present=False,
                status="legacy_not_applicable",
                contract_version="absent",
            )
            print(
                "repository verification: LEGACY_NOT_APPLICABLE (migration mode; contract absent)"
            )
        else:
            _write_result(
                result=CONTRACT_MISSING,
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
    version = "unknown"
    try:
        version = workflow.contract_version()
        if version == "v2":
            _execute_v2(workflow)
        elif mode == REQUIRED_MODE:
            raise WorkflowError(
                "V1 contract rejected in required mode; run l9-repo migrate-v1"
            )
        else:
            _execute_v1(workflow)
    except subprocess.CalledProcessError as exc:
        _write_result(
            result=TECHNICAL_FAILURE,
            present=True,
            status="technical_failure",
            contract_version=version,
            failure_kind="technical",
        )
        print(f"repository verification: TECHNICAL_FAILURE ({exc})", file=sys.stderr)
        return 0
    except Exception as exc:
        _write_result(
            result=CONTRACT_INVALID,
            present=True,
            status="contract_failure",
            contract_version=version,
            failure_kind="contract",
        )
        print(f"repository verification: CONTRACT_FAILURE ({exc})", file=sys.stderr)
        return 0

    if version == "v2":
        _write_result(
            result=V2_PASS, present=True, status="pass", contract_version=version
        )
        print("repository verification: PASS (v2; " + " -> ".join(PHASES) + ")")
    else:
        _write_result(
            result=V1_COMPAT, present=True, status="v1_compat", contract_version=version
        )
        print(
            "repository verification: V1_COMPAT (migration mode; "
            + " -> ".join(PHASES)
            + "; migrate with l9-repo migrate-v1)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
