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
every Core-owned file compared byte-for-byte with this Core checkout. Drift or
a malformed record is a contract failure in both modes, before any phase runs:
the record is the repository's own claim that those files are Core's, so the
claim is held to. A repository without a record is not stamp-checked at all,
so an unstamped fleet repository is unaffected (``AGENTS.md`` §25).

Before any contract decision, every tracked text file is checked for an
unresolved merge conflict: git's complete marker block (``<<<<<<<``, an
optional diff3 ``|||||||`` base, ``=======``, ``>>>>>>>``, each at column 0).
One fails the run as a ``contract`` failure in both modes, whether or not the
repository declares a contract. A lone ``=======`` or an indented sample is
not a conflict. The SDK runtime (``.l9/runtime``) is never scanned.

Every run records typed outputs: ``present``, ``status`` (``pass``,
``not_applicable``, or ``fail``), ``contract-mode``, ``contract-version``
(``absent``, ``v1``, ``v2``, or ``invalid``), and ``failure-kind`` (``none``,
``contract``, ``command``, or ``infrastructure``). Contract classification and
every execution decision are made by this immutable Core checkout, never by
consumer-vendored code.
"""

from __future__ import annotations

import base64
import collections
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator, MutableMapping
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[3]
CORE_TOOLS = CORE_ROOT / "tools"
if str(CORE_TOOLS) not in sys.path:
    sys.path.insert(0, str(CORE_TOOLS))

from l9_repo.__main__ import RepositoryWorkflow, WorkflowError  # noqa: E402
from l9_repo.contract import (  # noqa: E402
    CONTRACT_PATH,
    FAST_TEST_PHASE,
    PHASES,
    ContractError,
    RepositoryExecution,
    load_contract,
    validate_v2_contract_data,
)
from l9_repo.inherited_config import materialize, resolve  # noqa: E402
from l9_repo.stamp import classify_stamp  # noqa: E402

POLICY_PATH = Path(__file__).resolve().parent / "contract-policy.json"
POLICY_SCHEMA = "l9.repository-contract-enforcement/v1"
POLICY_KEYS = frozenset({"schema", "default_mode", "required_repositories"})

MIGRATION_MODE = "migration"
REQUIRED_MODE = "required"
MODES = frozenset({MIGRATION_MODE, REQUIRED_MODE})

V1_SCHEMA_VERSION = 1
V2_REQUIRED_PHASES = PHASES
#: Events whose required check judges pull-request test expense (org-ci.yml
#: "Judge pull-request test expense"). On these a make-v2 consumer runs
#: test-fast, the selection that judge proved bounded; every other event runs
#: the whole test phase.
FAST_TEST_EVENTS = frozenset(
    {"pull_request", "pull_request_target", "merge_group", "push"}
)

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
#: Set by this action's snapshot step, never by a consumer: a registry read
#: made BEFORE any consumer code runs, by a step that holds the read token. The
#: verify step (which runs the consumer's make targets) never holds the token.
REGISTRY_SNAPSHOT_ENV = "L9_REGISTRY_SNAPSHOT"
READ_TOKEN_ENV = "L9_GITHUB_READ_TOKEN"
_REGISTRY_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_YAML_SCALAR = re.compile(r"^(\s*)(- )?([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*?))?\s*$")

# Event context for the consumer's make (row 27). Empty: the consumer runs
# under this run's own GitHub context. ``push``: the consumer runs as a push of
# the commit checked out in the workspace to EVENT_REF_ENV. The promotion
# canary checks out a consumer's main SHA inside a Core pull_request run; the
# runner's GITHUB_* describe the Core PR, and a workflow ``env:`` cannot
# overwrite GITHUB_* (GitHub "Variables" reference). So the context is set
# here, in the process that starts the consumer's commands, before any of them.
EVENT_NAME_ENV = "L9_VERIFICATION_EVENT"
EVENT_REF_ENV = "L9_VERIFICATION_REF"
EVENT_PUSH = "push"
_BRANCH_REF = re.compile(r"^refs/heads/[A-Za-z0-9._/-]+$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ZERO_SHA = "0" * 40


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


def github_auth_env(token: str) -> dict[str, str]:
    """Environment that authenticates git to https://github.com/ with ``token``.

    git's GIT_CONFIG_COUNT/KEY/VALUE interface keeps the header off every
    command line. An empty token leaves git anonymous.
    """

    env = {key: value for key, value in os.environ.items() if key != READ_TOKEN_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not token:
        return env
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    index = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    env[f"GIT_CONFIG_KEY_{index}"] = "http.https://github.com/.extraheader"
    env[f"GIT_CONFIG_VALUE_{index}"] = f"AUTHORIZATION: basic {basic}"
    env["GIT_CONFIG_COUNT"] = str(index + 1)
    return env


def fetch_registry() -> str:
    """Read the registry: the pre-consumer snapshot if one was taken, else live.

    ``Quantum-L9/.github`` is private (2026-11-16), so the live read needs the
    read token, and the step that runs consumer code must not hold it. The
    action's snapshot step therefore reads the registry first and this step
    reads that file. Without a snapshot the live read runs as before.
    """

    snapshot = os.environ.get(REGISTRY_SNAPSHOT_ENV, "").strip()
    if snapshot and Path(snapshot).is_file():
        return Path(snapshot).read_text(encoding="utf-8")
    return fetch_live_registry(os.environ.get(READ_TOKEN_ENV, "").strip())


def fetch_live_registry(token: str = "") -> str:
    """Read the registry from its owner's default branch over Git.

    Runs in a throwaway repository under the runner's temporary directory; no
    consumer file and no consumer-supplied value selects the source.
    """

    auth_env = github_auth_env(token)
    with tempfile.TemporaryDirectory(prefix="l9-registry-") as scratch:

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            try:
                return subprocess.run(
                    ["git", "-C", scratch, *args],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=REGISTRY_TIMEOUT_SECONDS,
                    env=auth_env,
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
                f"cannot read {REGISTRY_PATH}: "
                + (shown.stderr.strip() or "git show failed")
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
    """Fail on a locally modified Core-owned file; warn on one merely behind.

    Integrity blocks in every mode: a stamped file the repository edited or
    removed. Drift is housekeeping and only warns (operator policy 2026-11-20):
    a file still carrying the bytes it was stamped with while Core moved on.
    ``mode`` is reported in the failure only. An unstamped repository is
    skipped.
    """

    record, findings, behind = classify_stamp(workspace)
    if record is None:
        return
    revision = record["core"]["revision"]
    for finding in behind:
        print(f"::warning::Core stamp behind Core (advisory): {finding}")
    if not findings:
        state = "aligned" if not behind else f"{len(behind)} file(s) behind Core"
        print(f"Core stamp: {state} ({record['profile']}; stamped from {revision})")
        report_inherited(workspace)
        return
    for finding in findings:
        print(f"::error::Core stamp modified locally: {finding}")
    raise ContractError(
        f"Core-owned files were modified locally ({mode} mode); re-stamp "
        "with `python3 -m tools.l9_repo --workspace . stamp --profile "
        f"{record['profile']}` from the Core revision org-ci.yml pins: "
        + "; ".join(findings)
    )


def report_inherited(workspace: Path) -> None:
    """State which centrally resolved configuration this run uses.

    Read-only. The digest is computed from this Core checkout's bytes, never
    from a consumer value, so it names the configuration actually resolved.
    """

    resolution = resolve(workspace)
    if resolution is None:
        return
    print(
        f"Core inherited configuration: {len(resolution.files)} file(s) resolved "
        f"from this Core revision; configuration digest "
        f"{resolution.configuration_digest}"
    )
    for relative, digest in resolution.digests.items():
        print(f"  {relative} {digest}")


def materialize_inherited(workspace: Path) -> None:
    """Write the inherited configuration before the repository's phases run.

    The repository does not carry these files; its tools read them from disk.
    ``verify_v2`` has already refused a local copy that differs, so this only
    adds absent files.
    """

    resolution = resolve(workspace)
    if resolution is None:
        return
    written = materialize(workspace, resolution)
    print(
        f"Core inherited configuration: materialized {len(written)} of "
        f"{len(resolution.files)} file(s)"
    )


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


class EventContextError(RuntimeError):
    """The requested event context cannot be established for the workspace."""


def _rev(workspace: Path, revision: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{revision}^{{commit}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and _FULL_SHA.match(value) else ""


def apply_event_context(
    workspace: Path, repository: str, environ: MutableMapping[str, str]
) -> str:
    """Set the GitHub context the consumer's commands inherit; return the event.

    Nothing changes when EVENT_NAME_ENV is empty. ``push`` is the only event
    that can be stated honestly for a checked-out commit: GITHUB_SHA is the
    workspace HEAD, GITHUB_REF the declared branch, the pull_request-only
    GITHUB_BASE_REF / GITHUB_HEAD_REF are cleared, and GITHUB_EVENT_PATH names
    a push payload written for that commit, not the caller's event file.
    """

    event = environ.get(EVENT_NAME_ENV, "").strip()
    environ.pop(EVENT_NAME_ENV, None)
    ref = environ.pop(EVENT_REF_ENV, "").strip()
    if not event:
        if ref:
            raise EventContextError(f"{EVENT_REF_ENV} is set without an event")
        return ""
    if event != EVENT_PUSH:
        raise EventContextError(
            f"unsupported verification event {event!r}; only {EVENT_PUSH!r}"
        )
    if not _BRANCH_REF.match(ref) or ".." in ref:
        raise EventContextError(f"{EVENT_REF_ENV} must be refs/heads/<branch>: {ref!r}")
    if not _REPOSITORY_NAME.match(repository):
        raise EventContextError(
            f"repository identity is not owner/name: {repository!r}"
        )
    head = _rev(workspace, "HEAD")
    if not head:
        raise EventContextError("the workspace HEAD is not a resolvable commit")
    before = _rev(workspace, "HEAD^1") or ZERO_SHA
    payload = {
        "ref": ref,
        "before": before,
        "after": head,
        "repository": {"full_name": repository},
    }
    handle, path = tempfile.mkstemp(
        prefix="l9-push-event-", suffix=".json", dir=environ.get("RUNNER_TEMP") or None
    )
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
    branch = ref.removeprefix("refs/heads/")
    environ.update(
        {
            "GITHUB_EVENT_NAME": EVENT_PUSH,
            "GITHUB_EVENT_PATH": path,
            "GITHUB_SHA": head,
            "GITHUB_REF": ref,
            "GITHUB_REF_NAME": branch,
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_BASE_REF": "",
            "GITHUB_HEAD_REF": "",
            "GITHUB_REPOSITORY": repository,
        }
    )
    print(f"repository verification: event context push {branch}@{head[:12]}")
    return EVENT_PUSH


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


TAIL_LINES = 40
TEE_JOIN_SECONDS = 10
FAILURE_SIDECAR = "l9-repository-verification-failure.json"
FAILURE_TAIL = "l9-repository-verification-tail.txt"


class PhaseError(subprocess.CalledProcessError):
    """A make phase that exited non-zero, with the tail of its output."""

    def __init__(self, phase: str, argv: list[str], code: int, tail: list[str]):
        super().__init__(code, argv, output="".join(tail))
        self.phase = phase
        self.tail = tail


@contextlib.contextmanager
def _tee_output(tail: collections.deque[str]) -> Iterator[None]:
    """Copy everything written to fds 1 and 2 through, keeping the last lines.

    The phase's own process inherits the redirected descriptors, so its
    output still streams to the log as it arrives; nothing about how it is
    started changes. The reader is joined with a timeout: a background
    process a phase leaves running keeps the pipe open and must not hang the
    step (the reader then keeps forwarding until that process exits).
    """
    sys.stdout.flush()
    sys.stderr.flush()
    read_fd, write_fd = os.pipe()
    saved_out, saved_err = os.dup(1), os.dup(2)

    def pump() -> None:
        pending = b""
        with os.fdopen(read_fd, "rb", buffering=0) as reader:
            while chunk := reader.read(65536):
                os.write(saved_out, chunk)
                pending += chunk
                *done, pending = pending.split(b"\n")
                tail.extend(line.decode("utf-8", "replace") + "\n" for line in done)
        if pending:
            tail.append(pending.decode("utf-8", "replace") + "\n")

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    os.dup2(write_fd, 1)
    os.dup2(write_fd, 2)
    os.close(write_fd)
    try:
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        reader.join(timeout=TEE_JOIN_SECONDS)
        os.close(saved_err)
        if not reader.is_alive():
            os.close(saved_out)


def run_phase(phase: str, argv: list[str], workspace: Path) -> None:
    """Run one phase exactly as before, keeping the last lines of its output.

    The phase runs as ``subprocess.run(argv, cwd=workspace, check=True)``; a
    non-zero exit is re-raised as :class:`PhaseError`, which names the phase
    and carries the output tail so the failure is actionable without a
    re-run (signal law, L9-SIGNAL-001).
    """
    tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
    failure: subprocess.CalledProcessError | None = None
    with _tee_output(tail):
        try:
            subprocess.run(argv, cwd=workspace, check=True)
        except subprocess.CalledProcessError as exc:
            failure = exc
    if failure is not None:
        raise PhaseError(phase, argv, failure.returncode, list(tail)) from failure


def clear_failure_sidecar() -> None:
    """Remove a sidecar a previous run (or a test inside this one) left."""
    temp = os.environ.get("RUNNER_TEMP", "").strip()
    if temp:
        for name in (FAILURE_SIDECAR, FAILURE_TAIL):
            Path(temp, name).unlink(missing_ok=True)


def report_phase_failure(error: subprocess.CalledProcessError, workspace: Path) -> None:
    """Name the failed command, its phase, exit code and output tail.

    The sidecar records the workspace it describes; the enforcement step
    trusts it only for its own GITHUB_WORKSPACE, so a sidecar written by a
    test that runs this script on a temporary workspace (Core's own suite
    runs inside its verification) can never name the wrong command.
    """
    command = (
        " ".join(str(part) for part in error.cmd)
        if isinstance(error.cmd, list)
        else str(error.cmd)
    )
    phase = getattr(error, "phase", "")
    tail = list(getattr(error, "tail", []))
    where = f"phase {phase}: " if phase else ""
    print(
        f"::error title=Repository verification::{where}`{command}` exited "
        f"{error.returncode}; the last {len(tail)} line(s) of its output follow"
    )
    if tail:
        print(f"::group::last {len(tail)} line(s) of `{command}`")
        sys.stdout.write("".join(tail))
        print("::endgroup::")
    # A sidecar, not an action output (the typed outputs are frozen): the
    # enforcement step reads it to name the cause in its own error.
    temp = os.environ.get("RUNNER_TEMP", "").strip()
    if temp:
        Path(temp, FAILURE_TAIL).write_text("".join(tail), encoding="utf-8")
        Path(temp, FAILURE_SIDECAR).write_text(
            json.dumps(
                {
                    "phase": phase,
                    "command": command,
                    "exit": error.returncode,
                    "workspace": str(workspace.resolve()),
                }
            )
            + "\n",
            encoding="utf-8",
        )


def v2_run_phases(facade: str, event_name: str) -> tuple[str, ...]:
    """The facade phases one verification run executes, in order.

    make-v1 runs its four phases on every event. make-v2 runs
    ``setup validate check test-fast`` on a judged event and
    ``setup validate check test`` on any other: a pull request never runs the
    whole suite, and the whole suite is never silently dropped elsewhere.
    """

    if facade != "make-v2":
        return V2_REQUIRED_PHASES
    final = FAST_TEST_PHASE if event_name in FAST_TEST_EVENTS else "test"
    return ("setup", "validate", "check", final)


def run_v2(workspace: Path, facade: str = "make-v1") -> None:
    """Execute the declared facade's phases in order, argv-only.

    The facade was verified byte-for-byte against this Core checkout before
    this runs, so each phase reaches exactly the repository's ``repo-<phase>``
    leaf. The first non-zero phase raises :class:`PhaseError` (its command,
    exit code and output tail), so later phases never run. No consumer
    Python is imported. The event is read after ``apply_event_context``, so
    a canary leg run as a push takes the push phases. ``facade`` is the one
    ``verify_v2`` already validated.
    """

    materialize_inherited(workspace)
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    for phase in v2_run_phases(facade, event_name):
        argv = ["make", phase]
        print("+", " ".join(argv), flush=True)
        run_phase(phase, argv, workspace)


COMPOSER_WORKFLOW = Path(".github/workflows/governance-pr.yml")
COMPOSER_REPOSITORY = "https://github.com/Quantum-L9/Cursor-Governance.git"
COMPOSER_FILES = (
    "ops/scripts/compose_pr_body.py",
    "ops/scripts/org_dotgithub.py",
)
COMPOSER_PIN = re.compile(
    r"^ *COMPOSER: Quantum-L9/Cursor-Governance@([0-9a-f]{40}) *$",
    re.MULTILINE,
)
COMPOSER_TIMEOUT_SECONDS = 60


class ComposerError(RuntimeError):
    """Raised when a declared composer pin cannot be fetched."""


def composer_pin(workflow_text: str) -> str | None:
    """Return the single composer pin, ``None`` when the line is absent.

    More than one pin is an error: the consumer's own fetch treats that file
    the same way, and a guessed pin would fetch the wrong files.
    """

    pins = COMPOSER_PIN.findall(workflow_text)
    if not pins:
        return None
    if len(pins) != 1:
        raise ComposerError(
            f"governance-pr.yml: expected exactly one COMPOSER pin, found {len(pins)}"
        )
    return pins[0]


def _redact(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "[redacted]")
    return text


def fetch_composer(pin: str, destination: Path, token: str) -> None:
    """Fetch only the two composer files at ``pin`` into ``destination``.

    The git child receives the token as a header via ``github_auth_env`` and
    never as an argument. ``destination`` matches the layout
    ``SEED_COMPOSER_ROOT`` consumers already read.
    """

    auth_env = github_auth_env(token)
    with tempfile.TemporaryDirectory(prefix="l9-composer-git-") as scratch:

        def git(*args: str, binary: bool = False) -> subprocess.CompletedProcess[bytes]:
            try:
                completed = subprocess.run(
                    ["git", "-C", scratch, *args],
                    capture_output=True,
                    check=False,
                    timeout=COMPOSER_TIMEOUT_SECONDS,
                    env=auth_env,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ComposerError(f"cannot run git: {error}") from error
            if not binary and completed.returncode != 0:
                detail = _redact(
                    completed.stderr.decode("utf-8", "replace").strip(), token
                )
                raise ComposerError(detail or f"git {args[0]} failed")
            return completed

        git("init", "-q")
        git(
            "fetch",
            "-q",
            "--depth=1",
            "--no-tags",
            COMPOSER_REPOSITORY,
            pin,
        )
        for relative in COMPOSER_FILES:
            shown = git("show", f"FETCH_HEAD:{relative}", binary=True)
            if shown.returncode != 0:
                detail = _redact(shown.stderr.decode("utf-8", "replace").strip(), token)
                raise ComposerError(
                    f"cannot read {relative}: {detail or 'git show failed'}"
                )
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(shown.stdout)
    (destination / ".pin").write_text(f"{pin}\n", encoding="utf-8")


def prefetch_composer(workspace: Path, destination: Path) -> int:
    """Fetch the pinned composer when the workspace declares exactly one pin.

    Pops the read token before returning, including on failure. A workspace
    with no ``governance-pr.yml``, or one with no COMPOSER line, writes an
    empty ``seed-root`` and does not fetch. A present pin that cannot be
    fetched fails this step so the verify step never runs consumer code with
    the token, and never runs it against a missing composer.
    """

    token = os.environ.pop(READ_TOKEN_ENV, "").strip()
    workflow = workspace / COMPOSER_WORKFLOW
    if not workflow.is_file():
        _write_output("seed-root", "")
        return 0
    try:
        pin = composer_pin(workflow.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ComposerError) as exc:
        print(f"::error::composer prefetch: {exc}", file=sys.stderr)
        return 1
    if pin is None:
        _write_output("seed-root", "")
        return 0
    try:
        fetch_composer(pin, destination, token)
    except ComposerError as exc:
        print(f"::error::composer prefetch: {exc}", file=sys.stderr)
        return 1
    _write_output("seed-root", str(destination))
    print(f"composer prefetch: {pin} -> {destination}")
    return 0


def snapshot_registry(destination: Path) -> int:
    """Write the live registry to ``destination``; never fails the job.

    On any read failure the destination is removed so the verify step falls
    back to its own live read and fails closed there exactly as before.
    """

    token = os.environ.pop(READ_TOKEN_ENV, "").strip()
    try:
        text = fetch_live_registry(token)
        parse_registry_members(text)
    except RegistryError as exc:
        destination.unlink(missing_ok=True)
        print(f"::warning::registry snapshot unavailable ({exc})", file=sys.stderr)
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print(f"registry snapshot: {REGISTRY_URL} {REGISTRY_REF} -> {destination}")
    return 0


# Unresolved merge conflicts: git writes `<<<<<<< <label>`, optionally a
# diff3 base section opened by `||||||| <label>`, then `=======` alone, then
# `>>>>>>> <label>`, each at column 0. Only a complete block is reported, so
# a lone `=======` (a text rule) or a quoted marker never is.
_MARKER_OPEN = re.compile(r"^<{7}(?: |$)")
_MARKER_BASE = re.compile(r"^\|{7}(?: |$)")
_MARKER_SPLIT = re.compile(r"^={7}$")
_MARKER_CLOSE = re.compile(r"^>{7}(?: |$)")


def conflict_marker_blocks(text: str) -> list[int]:
    """1-based line numbers of each complete conflict block's opening marker."""
    found: list[int] = []
    opened: int | None = None
    split = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _MARKER_OPEN.match(line):
            opened, split = number, False
        elif opened is not None and _MARKER_BASE.match(line):
            continue
        elif opened is not None and _MARKER_SPLIT.match(line):
            split = True
        elif opened is not None and split and _MARKER_CLOSE.match(line):
            found.append(opened)
            opened, split = None, False
    return found


# The marker scan reads the tree through its own handle on subprocess.run, so
# it stays distinct from phase execution (the `make` calls that tests observe
# by replacing `subprocess.run`). It runs git read-only and never consumer code.
_run_git = subprocess.run

# Never part of the repository under analysis: git metadata and the SDK
# runtime Core provisions into the workspace (`.l9/runtime`).
_UNSCANNED = (".git", ".l9/runtime")


def _candidate_files(workspace: Path) -> list[str]:
    """Tracked files of a git work tree; every file of a plain directory.

    A git work tree is read with ``git ls-files`` so untracked and ignored
    files are left out. A directory that is not a work tree has no tracked
    set, so every file in it is a candidate.
    """
    inside = _run_git(
        ["git", "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return sorted(
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file()
            and not any(
                path.relative_to(workspace).as_posix() == prefix
                or path.relative_to(workspace).as_posix().startswith(prefix + "/")
                for prefix in _UNSCANNED
            )
        )
    listing = _run_git(
        ["git", "-C", str(workspace), "ls-files", "-z", "--cached"],
        capture_output=True,
        check=False,
    )
    if listing.returncode != 0:
        raise RuntimeError(
            "cannot list tracked files: "
            + listing.stderr.decode("utf-8", "replace").strip()
        )
    return [
        raw.decode("utf-8", "surrogateescape")
        for raw in listing.stdout.split(b"\0")
        if raw
    ]


def conflict_markers(workspace: Path) -> list[tuple[str, int]]:
    """(path, line) of every unresolved conflict block in a text file.

    Raises ``RuntimeError`` when a work tree's file list cannot be read: an
    unreadable tree is never reported clean.
    """
    found: list[tuple[str, int]] = []
    for relative in _candidate_files(workspace):
        path = workspace / relative
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192] or b"<<<<<<<" not in data:
            continue
        text = data.decode("utf-8", "replace")
        found.extend((relative, line) for line in conflict_marker_blocks(text))
    return found


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--snapshot-registry":
        return snapshot_registry(Path(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--prefetch-composer":
        workspace = Path(os.environ.get("L9_REPOSITORY_WORKSPACE", ".")).resolve()
        return prefetch_composer(workspace, Path(sys.argv[2]))
    os.environ.pop(READ_TOKEN_ENV, None)
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

    # Before any contract decision: a tracked file with an unresolved merge
    # conflict fails every governed repository, contract or not.
    try:
        markers = conflict_markers(workspace)
    except (OSError, RuntimeError) as exc:
        _record(
            present=contract.is_file(),
            status="fail",
            mode=mode,
            version="unknown",
            failure=FAILURE_INFRASTRUCTURE,
        )
        print(f"repository verification: FAIL (infrastructure; {exc})", file=sys.stderr)
        return 0
    if markers:
        for relative, line in markers:
            print(
                f"::error file={relative},line={line}::unresolved merge conflict "
                "(<<<<<<< / ======= / >>>>>>>)"
            )
        _record(
            present=contract.is_file(),
            status="fail",
            mode=mode,
            version="unknown",
            failure=FAILURE_CONTRACT,
        )
        print(
            "repository verification: FAIL (contract; "
            f"{len(markers)} unresolved merge conflict block(s) in tracked files)",
            file=sys.stderr,
        )
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

    try:
        apply_event_context(workspace, repository, os.environ)
    except (EventContextError, OSError) as exc:
        _record(
            present=True,
            status="fail",
            mode=mode,
            version="unknown",
            failure=FAILURE_INFRASTRUCTURE,
        )
        print(f"repository verification: FAIL (infrastructure; {exc})", file=sys.stderr)
        return 0

    version = CONTRACT_INVALID
    clear_failure_sidecar()
    try:
        document = load_contract(contract)
        version = classify_contract(document)
        if version == CONTRACT_V2:
            verify_v2(workspace, mode)
            run_v2(workspace, str(document.get("facade", "make-v1")))
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
        report_phase_failure(exc, workspace)
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
