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
this pinned action, keyed by the runner-supplied repository identity, and then
from the live L9 member list (``semantics/repository_registry.yaml`` in
Quantum-L9/.github): a repository the static projection does not list is still
``required`` when the registry names it with ``lifecycle: current``. The
registry can only raise a repository to ``required``, never lower it; a
registry that cannot be read or parsed fails the run as infrastructure (fail
closed). ``migration`` (the default) tolerates an absent contract and admits
V1; ``required`` admits only V2 and fails an absent contract.

A V2 consumer that carries a Core stamp record (``.l9/core-stamp.json``) has
every Core-owned file compared byte-for-byte with this Core checkout. Drift is
a contract failure in ``required`` mode and a warning annotation in
``migration`` mode; a malformed record is a contract failure in both.

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
import tempfile
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
from l9_repo.stamp import verify_stamp  # noqa: E402

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

# The canonical L9 member list (AGENTS.md §17). Read live, at the registry
# owner's default branch, only for a repository the static projection does
# not already require.
REGISTRY_URL = "https://github.com/Quantum-L9/.github.git"
REGISTRY_REF = "refs/heads/main"
REGISTRY_PATH = "semantics/repository_registry.yaml"
REGISTRY_SCHEMA = "l9.repository-registry/v1"
REGISTRY_ARTIFACT = "l9.repository-registry/global@1"
REGISTRY_LIFECYCLES = frozenset({"current", "superseded", "retired"})
REGISTRY_REQUIRED_LIFECYCLE = "current"
REGISTRY_TIMEOUT_SECONDS = 60
_REGISTRY_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_YAML_SCALAR = re.compile(r"^(\s*)(- )?([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*?))?\s*$")


class PolicyError(RuntimeError):
    """Raised when Core's bundled enforcement policy is not admissible."""


class RegistryError(RuntimeError):
    """Raised when the L9 repository registry cannot be read or parsed."""


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


def _scalar(raw: str | None) -> str:
    value = (raw or "").split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def parse_registry_members(text: str) -> list[tuple[str, str]]:
    """Return ``(owner/name, lifecycle)`` for every registry entry.

    Standard library only: a strict reader for the registry's block-mapping
    shape, not a YAML parser. It validates the registry identity, then every
    entry under ``repositories:`` must carry an ``id``, a ``class_ref``, a
    known ``lifecycle`` and a GitHub ``coordinate``. Additional entry keys are
    skipped (the registry may grow fields); anything this reader cannot prove
    raises ``RegistryError`` so the caller fails closed.
    """

    top: dict[str, str] = {}
    entries: list[dict[str, str]] = []
    section = ""
    nested = ""
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            match = _YAML_SCALAR.match(line)
            if not match or match.group(2):
                section = "?"
                continue
            section = match.group(3)
            top[section] = _scalar(match.group(4))
            continue
        if section != "repositories":
            continue
        match = _YAML_SCALAR.match(line)
        if not match:
            if line.startswith("    ") and nested not in {"", "coordinate"}:
                continue  # a value of a skipped additional key
            raise RegistryError(f"registry line {number}: unreadable entry line")
        indent, item, key, value = match.groups()
        width = len(indent)
        if item is not None:
            if width >= 4 and nested not in {"", "coordinate"}:
                continue  # a list inside a skipped additional key
            if width != 2:
                raise RegistryError(f"registry line {number}: misplaced entry")
            entries.append({key: _scalar(value)})
            nested = ""
            continue
        if not entries:
            raise RegistryError(f"registry line {number}: field outside an entry")
        if width == 4:
            nested = key
            entries[-1][key] = _scalar(value)
        elif width == 6 and nested == "coordinate":
            entries[-1][f"coordinate.{key}"] = _scalar(value)
        elif width >= 6:
            continue  # nested content of a skipped additional key
        else:
            raise RegistryError(f"registry line {number}: unexpected indentation")
    if top.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(f"registry schema must be {REGISTRY_SCHEMA!r}")
    if top.get("artifact_id") != REGISTRY_ARTIFACT or top.get("canonical") != "true":
        raise RegistryError(f"registry must be the canonical {REGISTRY_ARTIFACT}")
    if not entries:
        raise RegistryError("registry lists no repositories")
    members: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        entry_id = entry.get("id", "")
        if not entry_id or entry_id in seen:
            raise RegistryError(f"registry entry id {entry_id!r} missing or duplicated")
        seen.add(entry_id)
        if entry.get("coordinate.provider") != "github":
            raise RegistryError(f"{entry_id}: coordinate must name provider github")
        owner = entry.get("coordinate.organization", "")
        name = entry.get("coordinate.repository", "")
        if not (_REGISTRY_NAME.match(owner) and _REGISTRY_NAME.match(name)):
            raise RegistryError(f"{entry_id}: invalid coordinate {owner}/{name}")
        if not entry.get("class_ref"):
            raise RegistryError(f"{entry_id}: every member needs a class_ref")
        lifecycle = entry.get("lifecycle", "")
        if lifecycle not in REGISTRY_LIFECYCLES:
            raise RegistryError(f"{entry_id}: unknown lifecycle {lifecycle!r}")
        members.append((f"{owner}/{name}", lifecycle))
    return members


def fetch_registry() -> str:
    """Read the registry from its owner's default branch over Git.

    Runs in a throwaway repository under the runner's temporary directory; no
    consumer file and no consumer-supplied value selects the source.
    """

    with tempfile.TemporaryDirectory(prefix="l9-registry-") as scratch:
        def git(*args: str) -> subprocess.CompletedProcess[str]:
            try:
                return subprocess.run(
                    ["git", "-C", scratch, *args],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=REGISTRY_TIMEOUT_SECONDS,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RegistryError(f"cannot run git: {error}") from error

        for args in (
            ("init", "-q"),
            ("fetch", "-q", "--depth=1", "--no-tags", REGISTRY_URL, REGISTRY_REF),
        ):
            result = git(*args)
            if result.returncode != 0:
                raise RegistryError(
                    f"cannot read {REGISTRY_URL} {REGISTRY_REF}: "
                    + (result.stderr.strip() or f"git {args[0]} failed")
                )
        shown = git("show", f"FETCH_HEAD:{REGISTRY_PATH}")
        if shown.returncode != 0:
            raise RegistryError(
                f"cannot read {REGISTRY_PATH}: " + (shown.stderr.strip() or "git show failed")
            )
        return shown.stdout


def registry_requires(repository: str, text: str) -> bool:
    """True when the registry names ``repository`` with ``lifecycle: current``.

    Registry coordinates are case-sensitive; the verifier's static list is
    matched case-insensitively, and so is this, so a case variant can never
    escape ``required``.
    """

    wanted = repository.casefold()
    return any(
        coordinate.casefold() == wanted and lifecycle == REGISTRY_REQUIRED_LIFECYCLE
        for coordinate, lifecycle in parse_registry_members(text)
    )


def resolve_enforcement(policy: dict[str, object], repository: str) -> str:
    """Static projection first; then the live registry; never lower a mode."""

    mode = resolve_mode(policy, repository)
    if mode == REQUIRED_MODE or not repository:
        return mode
    if registry_requires(repository, fetch_registry()):
        return REQUIRED_MODE
    return mode


def check_stamp(workspace: Path, mode: str) -> None:
    """Blocking stamp drift in required mode; warning annotations otherwise."""

    record, findings = verify_stamp(workspace)
    if record is None:
        return
    revision = record["core"]["revision"]
    if not findings:
        print(f"Core stamp: aligned ({record['profile']}; stamped from {revision})")
        return
    if mode == REQUIRED_MODE:
        raise ContractError(
            "Core-owned files drift from this Core revision (required mode): "
            + "; ".join(findings)
        )
    for finding in findings:
        print(f"::warning::Core stamp drift (migration mode, advisory): {finding}")


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


def verify_v2(workspace: Path, mode: str = MIGRATION_MODE) -> None:
    """Contract, byte-exact generated facade, Repo.mk ABI, and any Core stamp,
    without mutation."""

    RepositoryExecution(workspace).validate()
    check_stamp(workspace, mode)


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
        mode = resolve_enforcement(load_policy(), repository)
    except (PolicyError, RegistryError) as exc:
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
            verify_v2(workspace, mode)
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
