#!/usr/bin/env python3
"""Core-owned repository-execution tooling.

Three layers live here, deliberately separated:

``RepositoryExecution`` (``tools/l9_repo/contract.py``)
    The portable V2 protocol: the three-field contract, the versioned
    ``make-v1`` facade, generated-``Makefile`` drift detection, and structural
    validation of the repository-owned ``Repo.mk``. Any consumer, including
    Core, is verified through it.

``CoreRepositoryExecution``
    Core's own local policy (``.l9/core-repo-policy.json``): checksum
    manifest, authority wiring, targeted change gates, evidence-bearing
    ``agent-check``, ``status``, ``clean``, and the locked ``reconcile``. It is
    never part of the consumer protocol.

``RepositoryWorkflow``
    The bounded V1 compatibility executor consumed by the pinned admission
    bridge (``.github/actions/run-repository-verification/run.py``) for a
    repository that still declares ``schema_version: 1``. It runs that
    contract's ``commands`` matrices argv-only under the same executable
    allowlist and nothing more; the superseded V1 structural validation and
    the generated-adapter compiler it depended on are retired.

This module is loaded from a pinned Core checkout when a consumer is verified,
so consumer-vendored tooling cannot redefine parsing, validation, or facade
semantics. It owns no Git publication: branch, push, and pull-request policy
belong to Cursor-Governance and are neither implemented nor proxied here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from .change_policy import (
    ChangePolicyError,
    ChangedFileResolution,
    companion_findings,
    resolve_changed_files,
    select_gates,
)
from .contract import (
    CONTRACT_PATH,
    PHASES,
    V2_SCHEMA_NAME,
    ContractError,
    RepositoryExecution,
    load_contract,
)
from .contract_wiring import ContractWiringError, validate_contract_wiring
from .locking import LockBusy, single_flight
from .reporting import StepEvidence, redact_text, write_reports

CORE_POLICY_PATH = pathlib.Path(".l9/core-repo-policy.json")
CORE_POLICY_SCHEMA = "l9.core-repository-policy/v1"
COMMANDS = (
    "validate",
    "reconcile",
    "core-validate",
    "doctor",
    "change-policy",
    "agent-check",
    "status",
    "clean",
    "help",
)
MANIFEST_CHECK_ENV = "L9_MANIFEST_CHECK"
V1_SCHEMA_VERSION = 1

# A `sha256sum`-style manifest line is "<64 hex chars><two spaces><path>".
_SHA256_HEX_LENGTH = 64
_MANIFEST_FIELD_SEPARATOR = "  "
_MANIFEST_PATH_OFFSET = _SHA256_HEX_LENGTH + len(_MANIFEST_FIELD_SEPARATOR)
_MANIFEST_MIN_LINE_LENGTH = _MANIFEST_PATH_OFFSET + 1
# agent-check exits 2 when an infrastructure/configuration failure occurred.
_INFRASTRUCTURE_EXIT_CODE = 2
# Configured argv (Core change gates and bounded V1 matrices) is consumed
# argv-only. `argv[0]` may name only the workspace interpreter token
# (`@python`) or the pinned toolchain; anything else is rejected fail-closed so
# a repository contract can never smuggle arbitrary commands through the
# runner. Arguments are passed literally and never parsed by a shell.
_ALLOWED_COMMAND_EXECUTABLES = frozenset({"ruff", "mypy", "uv"})


class AgentCheckFailure(RuntimeError):
    """Raised after all checks run and one or more findings remain."""


class WorkflowError(RuntimeError):
    """Raised when repository policy, contract, or infrastructure is invalid."""


def _fail(message: str) -> NoReturn:
    raise WorkflowError(message)


def _require_dict(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(f"{path} must be an object")
    return value


def _require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{path} must be a boolean")
    return value


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{path} must be a non-empty string")
    if "\x00" in value:
        _fail(f"{path} must not contain NUL")
    return value


def _require_int(value: object, path: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _fail(f"{path} must be an integer >= {minimum}")
    return value


def _validate_keys(
    value: Mapping[str, object],
    path: str,
    *,
    required: set[str],
    allowed: set[str] | None = None,
) -> None:
    missing = sorted(required - set(value))
    if missing:
        _fail(f"{path} missing keys: {', '.join(missing)}")
    permitted = required if allowed is None else allowed
    extras = sorted(set(value) - permitted)
    if extras:
        _fail(f"{path} has unsupported keys: {', '.join(extras)}")


def _validate_strings(
    value: object,
    path: str,
    *,
    allow_empty: bool = False,
    allow_empty_items: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        _fail(f"{path} must be a {'' if allow_empty else 'non-empty '}array")
    result: list[str] = []
    for index, item in enumerate(value):
        if allow_empty_items and item == "":
            result.append(item)
            continue
        result.append(_require_string(item, f"{path}[{index}]"))
    if len(result) != len(set(result)):
        _fail(f"{path} must not contain duplicates")
    return result


def _validate_safe_relative_path(value: object, path: str) -> str:
    text = _require_string(value, path)
    candidate = pathlib.PurePosixPath(text)
    if candidate.is_absolute() or ".." in candidate.parts or text in {".", ""}:
        _fail(f"{path} must be a safe relative path")
    return text


def _validate_argv(value: object, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(f"{path} must be a non-empty argv array")
    return [
        _require_string(item, f"{path}[{index}]") for index, item in enumerate(value)
    ]


def _require_allowlisted_executable(argv: list[str], path: str) -> None:
    if argv[0] != "@python" and argv[0] not in _ALLOWED_COMMAND_EXECUTABLES:
        _fail(
            f"{path}[0] is outside the argv-only allowlist "
            f"(@python, {', '.join(sorted(_ALLOWED_COMMAND_EXECUTABLES))})"
        )


def _validate_argv_matrix(value: object, path: str) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        _fail(f"{path} must be a non-empty argv matrix")
    matrix: list[list[str]] = []
    for index, raw in enumerate(value):
        argv = _validate_argv(raw, f"{path}[{index}]")
        _require_allowlisted_executable(argv, f"{path}[{index}]")
        matrix.append(argv)
    return matrix


def render_argv(argv: Sequence[str]) -> list[str]:
    """Bind the ``@python`` token to the interpreter running this tool."""

    return [sys.executable if token == "@python" else token for token in argv]


# ---------------------------------------------------------------------------
# Checksum manifest
# ---------------------------------------------------------------------------


def manifest_check_enabled() -> bool:
    """Whether `MANIFEST.sha256` is verified.

    Enabled unless ``L9_MANIFEST_CHECK`` explicitly disables it. The escape
    hatch exists for bisects and salvage work on a knowingly drifted tree; it
    is not a way to land a change without regenerating the manifest.
    """

    return os.environ.get(MANIFEST_CHECK_ENV, "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def verify_checksum_manifest(
    root: pathlib.Path, relative: str = "MANIFEST.sha256"
) -> None:
    manifest = root / relative
    if manifest.is_symlink() or not manifest.is_file():
        _fail(f"missing checksum manifest: {manifest}")
    errors: list[str] = []
    seen: set[str] = set()
    entries = 0
    for line_number, raw in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        if (
            len(raw) < _MANIFEST_MIN_LINE_LENGTH
            or raw[_SHA256_HEX_LENGTH:_MANIFEST_PATH_OFFSET]
            != _MANIFEST_FIELD_SEPARATOR
        ):
            errors.append(f"{relative}:{line_number}: malformed checksum entry")
            continue
        entries += 1
        digest, name = raw[:_SHA256_HEX_LENGTH], raw[_MANIFEST_PATH_OFFSET:]
        if name in seen:
            errors.append(f"{relative}:{line_number}: duplicate path {name}")
            continue
        seen.add(name)
        if any(ch not in "0123456789abcdef" for ch in digest):
            errors.append(f"{relative}:{line_number}: invalid sha256 digest")
            continue
        candidate = pathlib.PurePosixPath(name)
        if candidate.is_absolute() or ".." in candidate.parts or name in {"", "."}:
            errors.append(f"{relative}:{line_number}: unsafe path {name!r}")
            continue
        path = root / candidate
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            resolved = path.resolve()
        if path.is_symlink() or root.resolve() not in resolved.parents:
            errors.append(f"{relative}:{line_number}: unsafe or symlinked file {name}")
            continue
        if not path.is_file():
            errors.append(f"{relative}:{line_number}: missing file {name}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            errors.append(f"{relative}:{line_number}: checksum mismatch for {name}")
    if entries == 0:
        errors.append(f"{relative}: checksum manifest is empty")
    if errors:
        _fail("checksum manifest validation failed: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Core-local policy (never part of the consumer contract)
# ---------------------------------------------------------------------------


def validate_core_policy_data(data: object) -> dict[str, Any]:
    """Validate Core's local policy document."""

    policy = _require_dict(data, "core policy")
    _validate_keys(
        policy,
        "core policy",
        required={
            "schema",
            "comparison_ref",
            "clean_paths",
            "automation",
            "change_policy",
            "agent_contracts",
            "reporting",
            "status",
            "authority",
        },
    )
    if policy["schema"] != CORE_POLICY_SCHEMA:
        _fail(f"core policy schema must be {CORE_POLICY_SCHEMA}")
    _require_string(policy["comparison_ref"], "comparison_ref")

    clean_paths = _validate_strings(
        policy["clean_paths"], "clean_paths", allow_empty=True
    )
    for index, item in enumerate(clean_paths):
        _validate_safe_relative_path(item, f"clean_paths[{index}]")

    automation = _require_dict(policy["automation"], "automation")
    _validate_keys(automation, "automation", required={"lock"})
    lock = _require_dict(automation["lock"], "automation.lock")
    _validate_keys(lock, "automation.lock", required={"name", "stale_after_seconds"})
    lock_name = _require_string(lock["name"], "automation.lock.name")
    if lock_name in {".", ".."} or pathlib.PurePath(lock_name).name != lock_name:
        _fail("automation.lock.name must be a safe simple file name")
    _require_int(lock["stale_after_seconds"], "automation.lock.stale_after_seconds")

    change_policy = _require_dict(policy["change_policy"], "change_policy")
    _validate_keys(
        change_policy,
        "change_policy",
        required={"gate_order", "gates", "companion_rules"},
    )
    gate_order = _validate_strings(
        change_policy["gate_order"], "change_policy.gate_order"
    )
    gates = _require_dict(change_policy["gates"], "change_policy.gates")
    if set(gate_order) != set(gates):
        _fail("change_policy.gate_order must name every gate exactly once")
    for gate_id in gate_order:
        gate = _require_dict(gates[gate_id], f"change_policy.gates.{gate_id}")
        _validate_keys(
            gate,
            f"change_policy.gates.{gate_id}",
            required={"match_any_prefix", "blocking", "commands"},
        )
        _validate_strings(
            gate["match_any_prefix"],
            f"change_policy.gates.{gate_id}.match_any_prefix",
            allow_empty_items=True,
        )
        _require_bool(gate["blocking"], f"change_policy.gates.{gate_id}.blocking")
        _validate_argv_matrix(
            gate["commands"], f"change_policy.gates.{gate_id}.commands"
        )

    rules = change_policy["companion_rules"]
    if not isinstance(rules, list) or not rules:
        _fail("change_policy.companion_rules must be a non-empty array")
    seen_rules: set[str] = set()
    for index, raw in enumerate(rules):
        label = f"change_policy.companion_rules[{index}]"
        rule = _require_dict(raw, label)
        _validate_keys(
            rule,
            label,
            required={"id", "match_any_prefix", "message"},
            allowed={
                "id",
                "match_any_prefix",
                "require_any_prefix",
                "require_all_paths",
                "message",
            },
        )
        rule_id = _require_string(rule["id"], f"{label}.id")
        if rule_id in seen_rules:
            _fail(f"duplicate companion rule id: {rule_id}")
        seen_rules.add(rule_id)
        _validate_strings(
            rule["match_any_prefix"],
            f"{label}.match_any_prefix",
            allow_empty_items=True,
        )
        has_requirement = False
        if "require_any_prefix" in rule:
            _validate_strings(
                rule["require_any_prefix"],
                f"{label}.require_any_prefix",
                allow_empty_items=True,
            )
            has_requirement = True
        if "require_all_paths" in rule:
            for path_index, relative in enumerate(
                _validate_strings(
                    rule["require_all_paths"], f"{label}.require_all_paths"
                )
            ):
                _validate_safe_relative_path(
                    relative, f"{label}.require_all_paths[{path_index}]"
                )
            has_requirement = True
        if not has_requirement:
            _fail(f"{label} must declare require_any_prefix or require_all_paths")
        _require_string(rule["message"], f"{label}.message")

    contracts = _require_dict(policy["agent_contracts"], "agent_contracts")
    _validate_keys(
        contracts,
        "agent_contracts",
        required={"required_files", "reference_requirements"},
    )
    for index, relative in enumerate(
        _validate_strings(contracts["required_files"], "agent_contracts.required_files")
    ):
        _validate_safe_relative_path(
            relative, f"agent_contracts.required_files[{index}]"
        )
    requirements = contracts["reference_requirements"]
    if not isinstance(requirements, list) or not requirements:
        _fail("agent_contracts.reference_requirements must be a non-empty array")
    for index, raw in enumerate(requirements):
        label = f"agent_contracts.reference_requirements[{index}]"
        requirement = _require_dict(raw, label)
        _validate_keys(requirement, label, required={"target", "instruction_files"})
        _validate_safe_relative_path(requirement["target"], f"{label}.target")
        for file_index, relative in enumerate(
            _validate_strings(
                requirement["instruction_files"], f"{label}.instruction_files"
            )
        ):
            _validate_safe_relative_path(
                relative, f"{label}.instruction_files[{file_index}]"
            )

    reporting = _require_dict(policy["reporting"], "reporting")
    _validate_keys(
        reporting,
        "reporting",
        required={"agent_check_json", "agent_check_markdown", "capture_limit_chars"},
    )
    _validate_safe_relative_path(
        reporting["agent_check_json"], "reporting.agent_check_json"
    )
    _validate_safe_relative_path(
        reporting["agent_check_markdown"], "reporting.agent_check_markdown"
    )
    _require_int(
        reporting["capture_limit_chars"], "reporting.capture_limit_chars", minimum=1000
    )

    status = _require_dict(policy["status"], "status")
    _validate_keys(status, "status", required={"fetch_remote"})
    _require_bool(status["fetch_remote"], "status.fetch_remote")

    authority = _require_dict(policy["authority"], "authority")
    _validate_keys(
        authority,
        "authority",
        required={
            "target_authorities",
            "derived_documents",
            "generated_artifacts",
            "dependency_manifests",
        },
    )
    for key in (
        "target_authorities",
        "derived_documents",
        "generated_artifacts",
        "dependency_manifests",
    ):
        for index, relative in enumerate(
            _validate_strings(authority[key], f"authority.{key}")
        ):
            _validate_safe_relative_path(relative, f"authority.{key}[{index}]")
    return policy


class CoreRepositoryExecution(RepositoryExecution):
    """Core's local policy layer on top of the portable V2 protocol."""

    def run(
        self,
        argv: Sequence[str],
        *,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=self.root,
            text=True,
            capture_output=capture,
            check=check,
        )

    def git(
        self,
        *args: str,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(["git", *args], capture=capture, check=check)

    def _ensure_repository_root(self) -> None:
        result = self.git("rev-parse", "--show-toplevel", capture=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            _fail(
                result.stderr.strip()
                or f"workspace is not a Git repository root: {self.root}"
            )
        actual = pathlib.Path(result.stdout.strip()).resolve()
        if actual != self.root:
            _fail(f"workspace is not repository root: {self.root} != {actual}")

    def has_policy(self) -> bool:
        path = self.root / CORE_POLICY_PATH
        return path.is_file() and not path.is_symlink()

    def policy(self) -> dict[str, Any]:
        path = self.root / CORE_POLICY_PATH
        if path.is_symlink() or not path.is_file():
            _fail(f"missing Core repository policy: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            _fail(f"invalid Core repository policy {path}: {error}")
        return validate_core_policy_data(data)

    # -- structural validation -------------------------------------------------

    def core_validate(self) -> None:
        """The portable V2 validation plus Core's own integrity policy."""

        self._ensure_repository_root()
        self.validate()
        policy = self.policy()
        if manifest_check_enabled():
            verify_checksum_manifest(self.root)
        try:
            validate_contract_wiring(self.root, policy["agent_contracts"])
        except ContractWiringError as error:
            _fail(str(error))
        errors: list[str] = []
        for key, label in (
            ("target_authorities", "target authority"),
            ("derived_documents", "derived document"),
            ("generated_artifacts", "generated artifact"),
            ("dependency_manifests", "dependency manifest"),
        ):
            for relative in policy["authority"][key]:
                path = self.root / relative
                if path.is_symlink() or not path.is_file():
                    errors.append(f"missing {label}: {relative}")
        if errors:
            _fail("; ".join(errors))
        print("core-validate: PASS")

    # -- change context --------------------------------------------------------

    def _comparison_ref(self, base_ref: str | None = None) -> str:
        if base_ref:
            return base_ref
        comparison = self.policy()["comparison_ref"]
        assert isinstance(comparison, str)
        return comparison

    def _resolve_changes(
        self,
        *,
        explicit: Sequence[str] = (),
        base_ref: str | None = None,
        head_ref: str = "HEAD",
    ) -> ChangedFileResolution:
        return resolve_changed_files(
            self.root,
            explicit=explicit,
            base_ref=self._comparison_ref(base_ref),
            head_ref=head_ref,
        )

    def change_policy(
        self,
        *,
        explicit: Sequence[str] = (),
        base_ref: str | None = None,
        head_ref: str = "HEAD",
    ) -> None:
        self._ensure_repository_root()
        resolution = self._resolve_changes(
            explicit=explicit, base_ref=base_ref, head_ref=head_ref
        )
        policy = self.policy()["change_policy"]
        findings = companion_findings(policy, resolution.files)
        payload = {
            "schema": "l9.repo-change-policy/v1",
            "change_context": {
                "source": resolution.source,
                "base_ref": resolution.base_ref,
                "head_ref": resolution.head_ref,
            },
            "changed_files": list(resolution.files),
            "selected_gates": [
                gate.gate_id for gate in select_gates(policy, resolution.files)
            ],
            "companion_findings": [
                {
                    "rule_id": finding.rule_id,
                    "message": finding.message,
                    "changed": list(finding.changed),
                    "required_any": list(finding.required_any),
                    "missing_all": list(finding.missing_all),
                }
                for finding in findings
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        if findings:
            raise AgentCheckFailure(
                f"change policy found {len(findings)} blocking companion finding(s)"
            )

    # -- evidence-bearing completion check -------------------------------------

    def worktree_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for argv in (
            ("git", "diff", "--binary", "HEAD"),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"),
        ):
            result = subprocess.run(
                list(argv), cwd=self.root, capture_output=True, check=False
            )
            if result.returncode != 0:
                _fail(
                    result.stderr.decode("utf-8", errors="replace").strip()
                    or "unable to fingerprint worktree"
                )
            digest.update(len(result.stdout).to_bytes(8, "big"))
            digest.update(result.stdout)
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        for raw in sorted(path for path in untracked.stdout.split(b"\0") if path):
            try:
                relative = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                _fail(f"non-UTF-8 untracked path: {error}")
            path = self.root / relative
            digest.update(raw)
            if path.is_symlink():
                digest.update(b"SYMLINK")
                digest.update(path.readlink().as_posix().encode("utf-8"))
            elif path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _bounded(text: str, limit: int) -> str:
        text = redact_text(text)
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n... output truncated at {limit} characters ...\n"

    @staticmethod
    def _classify_exit(returncode: int) -> str:
        if returncode == 0:
            return "pass"
        if returncode in {2, 126, 127}:
            return "infrastructure"
        return "finding"

    @staticmethod
    def classify_make_phase(returncode: int) -> str:
        """A completed facade phase either passed or produced a finding.

        GNU Make returns 2 for a failing recipe, so exit 2 from ``make <phase>``
        is repository evidence, not an infrastructure fault. Infrastructure is
        reserved for structural validation and worktree integrity, observed
        separately.
        """

        return "pass" if returncode == 0 else "finding"

    def agent_check(
        self,
        *,
        explicit: Sequence[str] = (),
        base_ref: str | None = None,
        head_ref: str = "HEAD",
    ) -> None:
        self._ensure_repository_root()
        policy = self.policy()
        resolution = self._resolve_changes(
            explicit=explicit, base_ref=base_ref, head_ref=head_ref
        )
        change_rules = policy["change_policy"]
        initial_subject_sha = self.git("rev-parse", "HEAD", capture=True).stdout.strip()
        initial_policy_sha256 = hashlib.sha256(
            (self.root / CORE_POLICY_PATH).read_bytes()
        ).hexdigest()
        initial_fingerprint = self.worktree_fingerprint()
        companion = companion_findings(change_rules, resolution.files)
        steps: list[StepEvidence] = []
        finding_failures = len(companion)
        infrastructure_failures = 0
        limit = policy["reporting"]["capture_limit_chars"]

        try:
            self.core_validate()
            steps.append(StepEvidence("structural-validate", tuple(), 0, "pass", True))
        except (WorkflowError, ContractError, ContractWiringError) as error:
            infrastructure_failures += 1
            steps.append(
                StepEvidence(
                    "structural-validate",
                    tuple(),
                    2,
                    "infrastructure",
                    True,
                    stderr=str(error),
                )
            )

        def record(
            name: str,
            argv: Sequence[str],
            *,
            blocking: bool,
            classify: Any,
        ) -> None:
            nonlocal finding_failures, infrastructure_failures
            print("+", " ".join(argv), flush=True)
            try:
                result = self.run(argv, capture=True, check=False)
            except OSError as error:
                infrastructure_failures += int(blocking)
                steps.append(
                    StepEvidence(
                        name,
                        tuple(argv),
                        127,
                        "infrastructure",
                        blocking,
                        stderr=str(error),
                    )
                )
                return
            stdout = self._bounded(result.stdout, limit)
            stderr = self._bounded(result.stderr, limit)
            if stdout:
                print(stdout, end="" if stdout.endswith("\n") else "\n")
            if stderr:
                print(
                    stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n"
                )
            classification = classify(result.returncode)
            if blocking and classification == "finding":
                finding_failures += 1
            if blocking and classification == "infrastructure":
                infrastructure_failures += 1
            steps.append(
                StepEvidence(
                    name,
                    tuple(argv),
                    result.returncode,
                    classification,
                    blocking,
                    stdout=stdout,
                    stderr=stderr,
                )
            )

        for gate in select_gates(change_rules, resolution.files):
            for index, configured in enumerate(gate.commands, start=1):
                record(
                    f"change-gate:{gate.gate_id}:{index}",
                    render_argv(configured),
                    blocking=gate.blocking,
                    classify=self._classify_exit,
                )
        for phase in ("validate", "check", "test"):
            record(
                f"make:{phase}",
                ("make", phase),
                blocking=True,
                classify=self.classify_make_phase,
            )

        integrity_errors: list[str] = []
        if (
            self.git("rev-parse", "HEAD", capture=True).stdout.strip()
            != initial_subject_sha
        ):
            integrity_errors.append("HEAD changed during agent-check")
        if (
            hashlib.sha256((self.root / CORE_POLICY_PATH).read_bytes()).hexdigest()
            != initial_policy_sha256
        ):
            integrity_errors.append("Core repository policy changed during agent-check")
        if self.worktree_fingerprint() != initial_fingerprint:
            integrity_errors.append("worktree content changed during agent-check")
        if integrity_errors:
            infrastructure_failures += 1
            steps.append(
                StepEvidence(
                    "non-mutation-check",
                    tuple(),
                    2,
                    "infrastructure",
                    True,
                    stderr="; ".join(integrity_errors),
                )
            )
        else:
            steps.append(StepEvidence("non-mutation-check", tuple(), 0, "pass", True))

        if infrastructure_failures:
            overall_exit_code = _INFRASTRUCTURE_EXIT_CODE
        elif finding_failures:
            overall_exit_code = 1
        else:
            overall_exit_code = 0
        json_path = self.root / policy["reporting"]["agent_check_json"]
        markdown_path = self.root / policy["reporting"]["agent_check_markdown"]
        write_reports(
            json_path,
            markdown_path,
            files=resolution.files,
            change_source=resolution.source,
            base_ref=resolution.base_ref,
            head_ref=resolution.head_ref,
            findings=[
                {
                    "rule_id": finding.rule_id,
                    "message": finding.message,
                    "changed": list(finding.changed),
                    "required_any": list(finding.required_any),
                    "missing_all": list(finding.missing_all),
                }
                for finding in companion
            ],
            steps=steps,
            overall_exit_code=overall_exit_code,
            subject_sha=initial_subject_sha,
            policy_sha256=initial_policy_sha256,
        )
        evidence = f"{json_path} and {markdown_path}"
        if overall_exit_code == _INFRASTRUCTURE_EXIT_CODE:
            raise WorkflowError(
                f"agent-check encountered {infrastructure_failures} "
                f"infrastructure/configuration failure(s); evidence: {evidence}"
            )
        if overall_exit_code == 1:
            raise AgentCheckFailure(
                f"agent-check found {finding_failures} blocking finding(s); "
                f"evidence: {evidence}"
            )
        print(f"agent-check: PASS; evidence: {evidence}")

    # -- local operations --------------------------------------------------------

    def doctor(self) -> None:
        self._ensure_repository_root()
        self.contract()
        # This tool owns local execution only. GitHub reachability and
        # credential state are publication concerns owned by Cursor-Governance,
        # so `gh` is neither required nor probed here.
        missing = sorted(tool for tool in ("git", "make") if not shutil.which(tool))
        if missing:
            _fail("missing tools: " + ", ".join(missing))
        print("doctor: PASS")

    def clean(self) -> None:
        self._ensure_repository_root()
        root = self.root.resolve()
        for relative in self.policy()["clean_paths"]:
            path = (root / relative).resolve()
            if path == root or root not in path.parents:
                _fail(f"unsafe clean path: {relative}")
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

    def _ref_exists(self, ref: str) -> bool:
        return (
            self.git("rev-parse", "--verify", ref, capture=True, check=False).returncode
            == 0
        )

    def status(self) -> None:
        self._ensure_repository_root()
        policy = self.policy()
        branch = self.git("branch", "--show-current", capture=True).stdout.strip()
        sha = self.git("rev-parse", "HEAD", capture=True).stdout.strip()
        fetch_result: subprocess.CompletedProcess[str] | None = None
        if policy["status"]["fetch_remote"]:
            try:
                fetch_result = self.git(
                    "fetch", "--prune", "origin", capture=True, check=False
                )
            except OSError as error:
                fetch_result = subprocess.CompletedProcess(
                    ["git", "fetch"], 127, "", str(error)
                )
        freshness = (
            "fresh"
            if fetch_result is not None and fetch_result.returncode == 0
            else "unknown_offline"
        )
        candidate_refs = []
        if branch:
            candidate_refs.append(f"origin/{branch}")
        candidate_refs.append(policy["comparison_ref"])
        comparison_ref = next(
            (ref for ref in candidate_refs if self._ref_exists(ref)), None
        )
        ahead: int | None = None
        behind: int | None = None
        if comparison_ref:
            counts = self.git(
                "rev-list",
                "--left-right",
                "--count",
                f"{comparison_ref}...HEAD",
                capture=True,
                check=False,
            )
            if counts.returncode == 0:
                values = counts.stdout.split()
                if len(values) == 2:
                    behind = int(values[0])
                    ahead = int(values[1])
        # Status is repository-local. Pull-request state lives on the
        # publication plane; aggregating the two belongs to Cursor-Governance.
        payload: dict[str, object] = {
            "branch": branch,
            "sha": sha,
            "dirty": bool(
                self.git("status", "--porcelain", capture=True).stdout.strip()
            ),
            "remote_freshness": freshness,
            "comparison_ref": comparison_ref,
            "comparison_source": (
                "unavailable"
                if comparison_ref is None
                else ("live" if freshness == "fresh" else "cached")
            ),
            "ahead": ahead,
            "behind": behind,
            "fetch_error": (
                None
                if fetch_result is None or fetch_result.returncode == 0
                else (fetch_result.stderr.strip() or "fetch failed")
            ),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))

    def _lock_settings(self) -> tuple[pathlib.Path, int]:
        lock = self.policy()["automation"]["lock"]
        value = self.git("rev-parse", "--git-path", lock["name"], capture=True).stdout
        path = pathlib.Path(value.strip())
        if not path.is_absolute():
            path = (self.root / path).resolve()
        return path, lock["stale_after_seconds"]

    def locked_reconcile(self) -> None:
        """Regenerate the facade under Core's single-flight lock when policy exists."""

        self._ensure_repository_root()
        if not self.has_policy():
            changed = self.reconcile()
            print("Makefile reconciled" if changed else "Makefile already current")
            return
        path, stale_after = self._lock_settings()
        try:
            with single_flight(path, stale_after=stale_after):
                changed = self.reconcile()
        except LockBusy as error:
            _fail(str(error))
        print("Makefile reconciled" if changed else "Makefile already current")


# ---------------------------------------------------------------------------
# Bounded V1 compatibility executor (admission bridge only)
# ---------------------------------------------------------------------------


def _v1_contract(data: Mapping[str, object]) -> dict[str, Any]:
    version = data.get("schema_version")
    if data.get("schema") == V2_SCHEMA_NAME or not (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == V1_SCHEMA_VERSION
    ):
        _fail("legacy V1 execution requires a schema_version 1 repository contract")
    return dict(data)


def _v1_matrix(data: Mapping[str, object], phase: str) -> list[list[str]]:
    if phase not in PHASES:
        _fail(f"unsupported repository execution phase: {phase}")
    commands = data.get("commands")
    if not isinstance(commands, dict) or phase not in commands:
        _fail(f"legacy V1 contract has no commands.{phase} matrix")
    return _validate_argv_matrix(commands[phase], f"commands.{phase}")


class RepositoryWorkflow:
    """Run a ``schema_version: 1`` contract's phases for the admission bridge.

    This is compatibility, not architecture: it executes the declared
    ``commands`` matrices for ``setup``, ``validate``, ``check``, and ``test``
    under the argv-only executable allowlist and nothing else. The superseded
    V1 structural validation (schema, capability plan, generated adapter
    parity) is retired with the compiler it depended on.
    """

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root.resolve()

    def run(
        self,
        argv: Sequence[str],
        *,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=self.root,
            text=True,
            capture_output=capture,
            check=check,
        )

    def _ensure_repository_root(self) -> None:
        result = self.run(
            ["git", "rev-parse", "--show-toplevel"], capture=True, check=False
        )
        if result.returncode != 0 or not result.stdout.strip():
            _fail(
                result.stderr.strip()
                or f"workspace is not a Git repository root: {self.root}"
            )
        actual = pathlib.Path(result.stdout.strip()).resolve()
        if actual != self.root:
            _fail(f"workspace is not repository root: {self.root} != {actual}")

    def contract(self) -> dict[str, Any]:
        try:
            return _v1_contract(load_contract(self.root / CONTRACT_PATH))
        except ContractError as error:
            _fail(str(error))

    def invoke(self, phase: str) -> None:
        self._ensure_repository_root()
        for configured in _v1_matrix(self.contract(), phase):
            argv = render_argv(configured)
            print("+", " ".join(argv), flush=True)
            self.run(argv)

    def setup(self) -> None:
        self.invoke("setup")

    def validate(self) -> None:
        self.invoke("validate")

    def check(self) -> None:
        self.invoke("check")

    def test(self) -> None:
        self.invoke("test")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l9-repo")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("command", choices=COMMANDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    core = CoreRepositoryExecution(arguments.workspace)
    try:
        if arguments.command == "validate":
            core.validate()
            print("validate: PASS")
        elif arguments.command == "reconcile":
            core.locked_reconcile()
        elif arguments.command == "core-validate":
            core.core_validate()
        elif arguments.command in {"change-policy", "agent-check"}:
            getattr(core, arguments.command.replace("-", "_"))(
                explicit=arguments.changed_file,
                base_ref=arguments.base_ref,
                head_ref=arguments.head_ref,
            )
        elif arguments.command == "help":
            print("Common commands: " + " ".join(COMMANDS))
        else:
            getattr(core, arguments.command)()
    except AgentCheckFailure as error:
        print(str(error), file=sys.stderr)
        return 1
    except (
        WorkflowError,
        ContractError,
        ChangePolicyError,
        ContractWiringError,
    ) as error:
        print(str(error), file=sys.stderr)
        return _INFRASTRUCTURE_EXIT_CODE
    except subprocess.CalledProcessError as error:
        return error.returncode or 1
    except OSError as error:
        print(str(error), file=sys.stderr)
        return _INFRASTRUCTURE_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
