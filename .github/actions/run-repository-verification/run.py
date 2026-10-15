#!/usr/bin/env python3
"""Run a consumer repository's canonical execution contract with pinned Core code.

This action is the organization admission point for repository execution and
reads two contract generations from ``.l9/repo-workflow.json``:

``schema_version == 1``
    The existing V1 contract. Parsing, validation, and command execution are
    delegated unchanged to this Core checkout's ``RepositoryWorkflow``. V1 is
    admitted only in ``migration`` enforcement mode.

``schema == "l9.repo-execution/v2"``
    The exact three-field V2 contract (``schema``, ``facade``,
    ``required_phases``). This Core checkout's ``l9_repo.contract`` validates
    the contract, verifies the consumer's generated ``Makefile`` byte-for-byte
    against the Core-owned facade, and checks the ``Repo.mk`` ABI before any
    phase runs. Execution then runs ``make setup -> validate -> check -> test``
    sequentially from the supplied workspace, argv-only, and stops at the first
    failing phase.

Any document carrying a ``schema`` key is judged as V2 and never falls through
into the V1 parser; anything else that is not ``schema_version == 1`` is an
invalid contract. An unknown generation is never guessed.

Whether a contract must be present is a Core enforcement decision, never a
repository one: the mode is resolved from ``contract-policy.json`` shipped with
this pinned action, keyed by the runner-supplied repository identity.
``migration`` (the default) tolerates an absent contract and admits V1;
``required`` admits only V2 and fails an absent contract.

Every run records typed outputs: ``present``, ``status`` (``pass``,
``not_applicable``, or ``fail``), ``contract-mode``, ``contract-version``
(``absent``, ``v1``, ``v2``, or ``invalid``), and ``failure-kind`` (``none``,
``contract``, ``command``, or ``infrastructure``). Contract classification and
every execution decision are made by this immutable Core checkout, never by
consumer-vendored code.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[3]
CORE_TOOLS = CORE_ROOT / "tools"
if str(CORE_TOOLS) not in sys.path:
    sys.path.insert(0, str(CORE_TOOLS))

from l9_repo.__main__ import RepositoryWorkflow, WorkflowError  # noqa: E402
from l9_repo.contract import (  # noqa: E402
    CONTRACT_PATH,
    PHASES,
    ContractError,
    RepositoryExecution,
    load_contract,
    validate_v2_contract_data,
)

POLICY_PATH = Path(__file__).resolve().parent / "contract-policy.json"
POLICY_SCHEMA = "l9.repository-contract-enforcement/v1"
POLICY_KEYS = frozenset({"schema", "default_mode", "required_repositories"})

MIGRATION_MODE = "migration"
REQUIRED_MODE = "required"
MODES = frozenset({MIGRATION_MODE, REQUIRED_MODE})

V1_SCHEMA_VERSION = 1
V2_REQUIRED_PHASES = PHASES

CONTRACT_ABSENT = "absent"
CONTRACT_V1 = "v1"
CONTRACT_V2 = "v2"
CONTRACT_INVALID = "invalid"

FAILURE_NONE = "none"
FAILURE_CONTRACT = "contract"
FAILURE_COMMAND = "command"
FAILURE_INFRASTRUCTURE = "infrastructure"

_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class PolicyError(RuntimeError):
    """Raised when Core's bundled enforcement policy is not admissible."""


def _write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def _record(
    *, present: bool, status: str, mode: str, version: str, failure: str
) -> None:
    _write_output("present", str(present).lower())
    _write_output("status", status)
    _write_output("contract-mode", mode)
    _write_output("contract-version", version)
    _write_output("failure-kind", failure)


def load_policy(path: Path | None = None) -> dict[str, object]:
    """Read and strictly validate Core's bundled enforcement policy."""

    path = POLICY_PATH if path is None else path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"unreadable enforcement policy {path}: {error}") from error
    if not isinstance(data, dict) or set(data) != POLICY_KEYS:
        raise PolicyError(
            "enforcement policy must be an object with exactly: "
            + ", ".join(sorted(POLICY_KEYS))
        )
    if data["schema"] != POLICY_SCHEMA:
        raise PolicyError(f"enforcement policy schema must be {POLICY_SCHEMA!r}")
    if data["default_mode"] not in MODES:
        raise PolicyError("enforcement policy default_mode is unsupported")
    repositories = data["required_repositories"]
    if not isinstance(repositories, list) or not all(
        isinstance(item, str) and _REPOSITORY_NAME.match(item) for item in repositories
    ):
        raise PolicyError(
            "enforcement policy required_repositories must be owner/name strings"
        )
    folded = [item.casefold() for item in repositories]
    if len(set(folded)) != len(folded):
        raise PolicyError("enforcement policy required_repositories has duplicates")
    return data


def resolve_mode(policy: dict[str, object], repository: str) -> str:
    """Return the enforcement mode Core assigns to ``repository``.

    The identity is the runner-supplied ``github.repository``; nothing the
    repository declares can select or weaken the mode.
    """

    required = policy["required_repositories"]
    assert isinstance(required, list)
    if repository and repository.casefold() in {item.casefold() for item in required}:
        return REQUIRED_MODE
    mode = policy["default_mode"]
    assert isinstance(mode, str)
    return mode


def classify_contract(data: dict[str, object]) -> str:
    """Return ``"v1"`` or ``"v2"``; any other generation is rejected.

    A document carrying ``schema`` is a V2 claim and is validated as V2 only:
    the V1 contract has no ``schema`` key, so a malformed V2 document can never
    be reinterpreted as V1.
    """

    if "schema" in data:
        validate_v2_contract_data(data)
        return CONTRACT_V2
    version = data.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        if version == V1_SCHEMA_VERSION:
            return CONTRACT_V1
    raise ContractError(
        "unrecognized repository contract generation: expected "
        f"schema_version == {V1_SCHEMA_VERSION} or an exact "
        "l9.repo-execution/v2 contract"
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


def verify_v2(workspace: Path) -> None:
    """Contract, byte-exact generated facade, and Repo.mk ABI, without mutation."""

    RepositoryExecution(workspace).validate()


def run_v2(workspace: Path) -> None:
    """Execute the declared ``make-v1`` facade phases in order, argv-only.

    The facade was verified byte-for-byte against this Core checkout before
    this runs, so each phase reaches exactly the repository's ``repo-<phase>``
    leaf. ``subprocess.run(check=True)`` raises on the first non-zero phase,
    so later phases never run. No consumer Python is imported.
    """

    for phase in V2_REQUIRED_PHASES:
        argv = ["make", phase]
        print("+", " ".join(argv), flush=True)
        subprocess.run(argv, cwd=workspace, check=True)


def main() -> int:
    workspace = Path(os.environ.get("L9_REPOSITORY_WORKSPACE", ".")).resolve()
    contract = workspace / CONTRACT_PATH
    repository = os.environ.get("L9_REPOSITORY_IDENTITY", "").strip()

    try:
        mode = resolve_mode(load_policy(), repository)
    except PolicyError as exc:
        _record(
            present=contract.is_file(),
            status="fail",
            mode="unresolved",
            version="unknown",
            failure=FAILURE_INFRASTRUCTURE,
        )
        print(f"repository verification: FAIL (infrastructure; {exc})", file=sys.stderr)
        return 0

    if not contract.is_file():
        if mode == MIGRATION_MODE:
            _record(
                present=False,
                status="not_applicable",
                mode=mode,
                version=CONTRACT_ABSENT,
                failure=FAILURE_NONE,
            )
            print(
                "repository verification: NOT_APPLICABLE "
                "(migration mode; .l9/repo-workflow.json absent)"
            )
        else:
            _record(
                present=False,
                status="fail",
                mode=mode,
                version=CONTRACT_ABSENT,
                failure=FAILURE_CONTRACT,
            )
            print(
                "repository verification: FAIL (contract; required mode and "
                ".l9/repo-workflow.json absent)",
                file=sys.stderr,
            )
        return 0

    version = CONTRACT_INVALID
    try:
        version = classify_contract(load_contract(contract))
        if version == CONTRACT_V2:
            verify_v2(workspace)
            run_v2(workspace)
        elif mode == REQUIRED_MODE:
            raise ContractError(
                "a V1 contract is not admitted in required mode; declare the "
                "l9.repo-execution/v2 contract"
            )
        else:
            run_v1(workspace)
    except subprocess.CalledProcessError as exc:
        failure = FAILURE_COMMAND
        detail: object = exc
    except (ContractError, WorkflowError) as exc:
        failure = FAILURE_CONTRACT
        detail = exc
    except Exception as exc:
        failure = FAILURE_INFRASTRUCTURE
        detail = exc
    else:
        _record(
            present=True,
            status="pass",
            mode=mode,
            version=version,
            failure=FAILURE_NONE,
        )
        print(
            f"repository verification: PASS ({mode}; {version}; "
            "setup + validate + check + test)"
        )
        return 0

    # Expected repository-verification failures become typed evidence so the
    # existing SDK/Semgrep analysis can still finish. The workflow's final
    # enforcement step fails blocking profiles on any non-pass typed result.
    _record(present=True, status="fail", mode=mode, version=version, failure=failure)
    print(
        f"repository verification: FAIL ({failure}; {mode}; {version}; {detail})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
