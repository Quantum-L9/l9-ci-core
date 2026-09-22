#!/usr/bin/env python3
"""Core-owned repository-execution contract tooling.

The portable consumer contract is deliberately declarative.  It names a fixed
Make ABI; it never carries repository commands.  This module is loaded from a
pinned Core checkout when a consumer is verified, so consumer-vendored tooling
cannot redefine parsing, validation, or generated-facade semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
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
from .contract_wiring import ContractWiringError, validate_contract_wiring
from .locking import LockBusy, single_flight
from .reporting import StepEvidence, redact_text, write_reports

PHASES = ("setup", "validate", "check", "test")
V2_SCHEMA_NAME = "l9.repo-execution/v2"
V2_FACADE_NAME = "make-v1"
CONFIG_PATH = pathlib.Path(".l9/repo-workflow.json")
CORE_POLICY_PATH = pathlib.Path(".l9/core-repo-policy.json")
PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_ROOT / "repo-workflow-v2.schema.json"
TEMPLATE_PATH = PACKAGE_ROOT / "Makefile.template"
MANIFEST_CHECK_ENV = "L9_MANIFEST_CHECK"
_INFRASTRUCTURE_EXIT_CODE = 2
_ALLOWED_LEGACY_EXECUTABLES = frozenset({"ruff", "mypy", "uv"})

COMMANDS = (
    "init",
    "reconcile",
    "validate",
    "verify-generated",
    "migrate-v1",
    "core-validate",
    "doctor",
    "change-policy",
    "agent-check",
    "status",
    "clean",
    "help",
)


class AgentCheckFailure(RuntimeError):
    """Raised after every eligible Core check ran and findings remain."""


class WorkflowError(RuntimeError):
    """Raised for malformed contracts and infrastructure failures."""


def _fail(message: str) -> NoReturn:
    raise WorkflowError(message)


def _require_dict(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(f"{path} must be an object")
    return value


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _fail(f"{path} must be a non-empty string without NUL")
    return value


def _require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{path} must be a boolean")
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


def _validate_strings(value: object, path: str, *, non_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value):
        _fail(f"{path} must be a {'non-empty ' if non_empty else ''}array")
    result = [
        _require_string(item, f"{path}[{index}]") for index, item in enumerate(value)
    ]
    if len(result) != len(set(result)):
        _fail(f"{path} must not contain duplicates")
    return result


def _validate_safe_relative_path(value: object, path: str) -> str:
    text = _require_string(value, path)
    candidate = pathlib.PurePosixPath(text)
    if candidate.is_absolute() or ".." in candidate.parts or text in {".", ""}:
        _fail(f"{path} must be a safe relative path")
    return text


def _load_json(path: pathlib.Path, description: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        _fail(f"missing {description}: {path}")
    try:
        return _require_dict(json.loads(path.read_text(encoding="utf-8")), description)
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"invalid {description}: {error}")


def validate_v2_contract_data(data: object) -> dict[str, Any]:
    """Validate only the portable V2 repository-execution declaration."""

    contract = _require_dict(data, "repo-workflow/v2")
    required = {"schema", "facade", "required_phases"}
    _validate_keys(contract, "repo-workflow/v2", required=required)
    if contract["schema"] != V2_SCHEMA_NAME:
        _fail(f"repo-workflow/v2.schema must be {V2_SCHEMA_NAME}")
    if contract["facade"] != V2_FACADE_NAME:
        _fail(f"repo-workflow/v2.facade must be {V2_FACADE_NAME}")
    phases = _validate_strings(
        contract["required_phases"], "repo-workflow/v2.required_phases"
    )
    if tuple(phases) != PHASES:
        _fail(f"repo-workflow/v2.required_phases must be {list(PHASES)} in order")
    return contract


# The former public helper is retained as a stable import name, but its meaning
# is now V2-only.  It deliberately accepts no V1 fields.
validate_config_data = validate_v2_contract_data


def _validate_v2_json_schema(contract: dict[str, Any]) -> None:
    schema = _load_json(SCHEMA_PATH, "Core-owned V2 schema")
    if schema.get("$id") != "https://quantum-l9.dev/schemas/repository-execution/v2":
        _fail("unexpected Core-owned V2 schema identity")
    try:
        import jsonschema

        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(contract)
    except ModuleNotFoundError:
        _fail(
            "jsonschema is required for V2 validation; install Core runtime requirements"
        )
    except Exception as error:
        _fail(f"repo-workflow/v2 schema validation failed: {error}")


def _contract_kind(data: Mapping[str, object]) -> str:
    if data.get("schema") == V2_SCHEMA_NAME:
        return "v2"
    if data.get("schema_version") == 1:
        return "v1"
    _fail("unsupported repository execution contract; expected V2 or supported V1")


def _legacy_matrix(data: Mapping[str, object], phase: str) -> list[list[str]]:
    commands = data.get("commands")
    if not isinstance(commands, dict) or not isinstance(commands.get(phase), list):
        _fail(f"legacy V1 contract has no commands.{phase} matrix")
    raw_matrix = commands[phase]
    if not raw_matrix:
        _fail(f"legacy V1 commands.{phase} must not be empty")
    matrix: list[list[str]] = []
    for command_index, raw_argv in enumerate(raw_matrix):
        if not isinstance(raw_argv, list) or not raw_argv:
            _fail(
                f"legacy V1 commands.{phase}[{command_index}] must be a non-empty argv"
            )
        argv = [
            _require_string(
                value, f"legacy V1 commands.{phase}[{command_index}][{index}]"
            )
            for index, value in enumerate(raw_argv)
        ]
        if argv[0] != "@python" and argv[0] not in _ALLOWED_LEGACY_EXECUTABLES:
            _fail("legacy V1 executable is outside the bounded compatibility allowlist")
        matrix.append(argv)
    return matrix


def _legacy_to_repo_mk(data: Mapping[str, object]) -> str:
    """Convert a bounded V1 command matrix into a repository-owned Make leaf."""

    lines = [
        "# Generated once from a legacy V1 execution contract.",
        "# Review and maintain repository implementation here after migration.",
        "PYTHON ?= python3",
        "",
        ".PHONY: " + " ".join(f"repo-{phase}" for phase in PHASES),
        "",
    ]
    for phase in PHASES:
        lines.append(f"repo-{phase}:")
        for argv in _legacy_matrix(data, phase):
            rendered = ["$(PYTHON)" if token == "@python" else token for token in argv]
            # Make recipes are shell text. Quote each consumer-supplied V1 token
            # before writing the consumer-owned implementation boundary.
            quoted = " ".join(_make_quote(token) for token in rendered)
            lines.append(f"\t{quoted}")
        lines.append("")
    return "\n".join(lines)


def _make_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=+@%,-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def manifest_check_enabled() -> bool:
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
    entries = 0
    seen: set[str] = set()
    for line_number, raw in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw.strip():
            continue
        if len(raw) < 67 or raw[64:66] != "  ":
            errors.append(f"{relative}:{line_number}: malformed checksum entry")
            continue
        entries += 1
        digest, name = raw[:64], raw[66:]
        if name in seen:
            errors.append(f"{relative}:{line_number}: duplicate path {name}")
            continue
        seen.add(name)
        path = root / pathlib.PurePosixPath(name)
        if (
            any(ch not in "0123456789abcdef" for ch in digest)
            or path.is_symlink()
            or not path.is_file()
            or root.resolve() not in path.resolve().parents
        ):
            errors.append(f"{relative}:{line_number}: unsafe or missing file {name}")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"{relative}:{line_number}: checksum mismatch for {name}")
    if entries == 0:
        errors.append(f"{relative}: checksum manifest is empty")
    if errors:
        _fail("checksum manifest validation failed: " + "; ".join(errors))


class RepositoryWorkflow:
    """V2 compiler, validator, and non-mutating generated-facade verifier."""

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
            list(argv), cwd=self.root, text=True, capture_output=capture, check=check
        )

    def git(
        self, *args: str, capture: bool = False, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self.run(["git", *args], capture=capture, check=check)

    def _ensure_repository_root(self) -> None:
        result = self.git("rev-parse", "--show-toplevel", capture=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            _fail(
                result.stderr.strip()
                or f"workspace is not a Git repository root: {self.root}"
            )
        if pathlib.Path(result.stdout.strip()).resolve() != self.root:
            _fail(f"workspace is not repository root: {self.root}")

    def raw_contract(self) -> dict[str, Any]:
        return _load_json(self.root / CONFIG_PATH, "repository execution contract")

    def contract_version(self) -> str:
        return _contract_kind(self.raw_contract())

    def config(self) -> dict[str, Any]:
        contract = self.raw_contract()
        if _contract_kind(contract) != "v2":
            _fail("V2 contract required for this operation; run l9-repo migrate-v1")
        return validate_v2_contract_data(contract)

    def render_makefile(self) -> bytes:
        if not TEMPLATE_PATH.is_file():
            _fail(f"missing Core canonical Makefile template: {TEMPLATE_PATH}")
        return TEMPLATE_PATH.read_bytes()

    def _required_repo_targets(self) -> None:
        path = self.root / "Repo.mk"
        if path.is_symlink() or not path.is_file():
            _fail("missing repository implementation boundary: Repo.mk")
        text = path.read_text(encoding="utf-8", errors="replace")
        for phase in PHASES:
            if not re.search(rf"(?m)^repo-{phase}\s*:", text):
                _fail(
                    f"missing required repository implementation target: repo-{phase}"
                )

    def validate(self) -> None:
        self._ensure_repository_root()
        contract = self.config()
        _validate_v2_json_schema(contract)

    def verify_generated(self) -> None:
        """Verify derived artifacts without writing the consumer worktree."""

        self.validate()
        makefile = self.root / "Makefile"
        if makefile.is_symlink() or not makefile.is_file():
            _fail("missing generated Makefile; run l9-repo reconcile")
        if makefile.read_bytes() != self.render_makefile():
            _fail("Makefile drift: run l9-repo reconcile")
        self._required_repo_targets()

    def reconcile(self) -> None:
        self._ensure_repository_root()
        self.validate()
        (self.root / "Makefile").write_bytes(self.render_makefile())
        print("Makefile reconciled from the Core canonical template")

    def init(self) -> None:
        self._ensure_repository_root()
        contract_path = self.root / CONFIG_PATH
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        if contract_path.exists():
            self.validate()
        else:
            contract_path.write_text(
                json.dumps(
                    {
                        "schema": V2_SCHEMA_NAME,
                        "facade": V2_FACADE_NAME,
                        "required_phases": list(PHASES),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        repo_mk = self.root / "Repo.mk"
        if not repo_mk.exists():
            repo_mk.write_text(
                "# Repository-owned implementation of the Core Make ABI.\n"
                "# Replace explicit no-op leaves with this repository's commands.\n"
                ".PHONY: repo-setup repo-validate repo-check repo-test\n\n"
                + "\n\n".join(f"repo-{phase}:\n\t@:" for phase in PHASES)
                + "\n",
                encoding="utf-8",
            )
        (self.root / "Makefile").write_bytes(self.render_makefile())
        print("repository execution contract initialized")

    def migrate_v1(self) -> None:
        self._ensure_repository_root()
        legacy = self.raw_contract()
        if _contract_kind(legacy) != "v1":
            _fail("migrate-v1 requires a supported V1 repository execution contract")
        repo_mk = self.root / "Repo.mk"
        if not repo_mk.exists():
            repo_mk.write_text(_legacy_to_repo_mk(legacy), encoding="utf-8")
        else:
            self._required_repo_targets()
        (self.root / CONFIG_PATH).write_text(
            json.dumps(
                {
                    "schema": V2_SCHEMA_NAME,
                    "facade": V2_FACADE_NAME,
                    "required_phases": list(PHASES),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.root / "Makefile").write_bytes(self.render_makefile())
        self.verify_generated()
        print("V1 contract migrated to V2; review Repo.mk before committing")

    def execute_phase(self, phase: str) -> None:
        if phase not in PHASES:
            _fail(f"unsupported repository execution phase: {phase}")
        self.verify_generated()
        print(f"+ make {phase}", flush=True)
        self.run(["make", phase])

    def execute_legacy_phase(self, phase: str) -> None:
        """Bounded, temporary V1 compatibility path.

        V1 execution is intentionally isolated here.  V2 never consumes command
        matrices; `migrate-v1` translates those matrices into `Repo.mk`.
        """

        if self.contract_version() != "v1":
            _fail("legacy execution requested for a non-V1 contract")
        for configured_argv in _legacy_matrix(self.raw_contract(), phase):
            argv = [
                sys.executable if token == "@python" else token
                for token in configured_argv
            ]
            print("+", " ".join(argv), flush=True)
            self.run(argv)


def _validate_command_matrix(value: object, path: str) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        _fail(f"{path} must be a non-empty argv matrix")
    result: list[list[str]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, list) or not raw:
            _fail(f"{path}[{index}] must be a non-empty argv")
        argv = [_require_string(item, f"{path}[{index}]") for item in raw]
        if argv[0] != "@python" and argv[0] not in _ALLOWED_LEGACY_EXECUTABLES:
            _fail(f"{path}[{index}][0] is outside Core's command allowlist")
        result.append(argv)
    return result


def validate_core_policy_data(data: object) -> dict[str, Any]:
    """Validate Core-local policy that is intentionally absent from V2."""

    policy = _require_dict(data, "core repository policy")
    required = {
        "schema",
        "comparison_ref",
        "clean_paths",
        "automation",
        "change_policy",
        "agent_contracts",
        "reporting",
        "status",
        "authority",
    }
    _validate_keys(policy, "core repository policy", required=required)
    if policy["schema"] != "l9.core-repository-policy/v1":
        _fail("unsupported Core repository policy schema")
    _require_string(policy["comparison_ref"], "comparison_ref")
    for index, item in enumerate(
        _validate_strings(policy["clean_paths"], "clean_paths", non_empty=False)
    ):
        _validate_safe_relative_path(item, f"clean_paths[{index}]")

    automation = _require_dict(policy["automation"], "automation")
    _validate_keys(automation, "automation", required={"lock"})
    lock = _require_dict(automation["lock"], "automation.lock")
    _validate_keys(lock, "automation.lock", required={"name", "stale_after_seconds"})
    name = _require_string(lock["name"], "automation.lock.name")
    if pathlib.PurePath(name).name != name or name in {".", ".."}:
        _fail("automation.lock.name must be a safe simple file name")
    _require_int(lock["stale_after_seconds"], "automation.lock.stale_after_seconds")

    policy_block = _require_dict(policy["change_policy"], "change_policy")
    _validate_keys(
        policy_block,
        "change_policy",
        required={"gate_order", "gates", "companion_rules"},
    )
    gate_order = _validate_strings(
        policy_block["gate_order"], "change_policy.gate_order"
    )
    gates = _require_dict(policy_block["gates"], "change_policy.gates")
    if set(gate_order) != set(gates):
        _fail("change_policy.gate_order must name every gate exactly once")
    for gate_id in gate_order:
        gate = _require_dict(gates[gate_id], f"change_policy.gates.{gate_id}")
        _validate_keys(
            gate,
            f"change_policy.gates.{gate_id}",
            required={"match_any_prefix", "blocking", "commands"},
        )
        prefixes = gate["match_any_prefix"]
        if (
            not isinstance(prefixes, list)
            or not prefixes
            or not all(isinstance(x, str) for x in prefixes)
        ):
            _fail(
                f"change_policy.gates.{gate_id}.match_any_prefix must be a non-empty string array"
            )
        _require_bool(gate["blocking"], f"change_policy.gates.{gate_id}.blocking")
        _validate_command_matrix(
            gate["commands"], f"change_policy.gates.{gate_id}.commands"
        )

    rules = policy_block["companion_rules"]
    if not isinstance(rules, list) or not rules:
        _fail("change_policy.companion_rules must be a non-empty array")
    for index, raw in enumerate(rules):
        rule = _require_dict(raw, f"change_policy.companion_rules[{index}]")
        allowed = {
            "id",
            "match_any_prefix",
            "require_any_prefix",
            "require_all_paths",
            "message",
        }
        _validate_keys(
            rule,
            f"change_policy.companion_rules[{index}]",
            required={"id", "match_any_prefix", "message"},
            allowed=allowed,
        )
        _require_string(rule["id"], f"change_policy.companion_rules[{index}].id")
        _require_string(
            rule["message"], f"change_policy.companion_rules[{index}].message"
        )
        for key in ("match_any_prefix", "require_any_prefix", "require_all_paths"):
            if key not in rule:
                continue
            values = rule[key]
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(x, str) for x in values)
            ):
                _fail(
                    f"change_policy.companion_rules[{index}].{key} must be a non-empty string array"
                )
        if "require_any_prefix" not in rule and "require_all_paths" not in rule:
            _fail(f"change_policy.companion_rules[{index}] must declare a requirement")

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
    if not isinstance(requirements, list):
        _fail("agent_contracts.reference_requirements must be an array")
    for index, raw in enumerate(requirements):
        requirement = _require_dict(
            raw, f"agent_contracts.reference_requirements[{index}]"
        )
        _validate_keys(
            requirement,
            f"agent_contracts.reference_requirements[{index}]",
            required={"target", "instruction_files"},
        )
        _validate_safe_relative_path(
            requirement["target"],
            f"agent_contracts.reference_requirements[{index}].target",
        )
        _validate_strings(
            requirement["instruction_files"],
            f"agent_contracts.reference_requirements[{index}].instruction_files",
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
        required={"target_authorities", "derived_documents", "generated_artifacts"},
    )
    for key in ("target_authorities", "derived_documents", "generated_artifacts"):
        for index, relative in enumerate(
            _validate_strings(authority[key], f"authority.{key}")
        ):
            _validate_safe_relative_path(relative, f"authority.{key}[{index}]")
    return policy


class CoreRepositoryWorkflow(RepositoryWorkflow):
    """Core's local policy layer; never used as a consumer protocol."""

    def policy(self) -> dict[str, Any]:
        return validate_core_policy_data(
            _load_json(self.root / CORE_POLICY_PATH, "Core repository policy")
        )

    def _comparison_ref(self, base_ref: str | None = None) -> str:
        return base_ref or self.policy()["comparison_ref"]

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

    def structural_validate(self) -> None:
        self.verify_generated()
        policy = self.policy()
        if manifest_check_enabled():
            verify_checksum_manifest(self.root)
        try:
            validate_contract_wiring(self.root, policy["agent_contracts"])
        except ContractWiringError as error:
            _fail(str(error))
        errors: list[str] = []
        for key in ("target_authorities", "derived_documents", "generated_artifacts"):
            for relative in policy["authority"][key]:
                path = self.root / relative
                if path.is_symlink() or not path.is_file():
                    errors.append(f"missing Core policy authority: {relative}")
        if errors:
            _fail("; ".join(errors))

    def _lock_settings(self) -> tuple[pathlib.Path, int]:
        lock = self.policy()["automation"]["lock"]
        path = self.git(
            "rev-parse", "--git-path", lock["name"], capture=True
        ).stdout.strip()
        candidate = pathlib.Path(path)
        return (
            candidate if candidate.is_absolute() else self.root / candidate,
            lock["stale_after_seconds"],
        )

    @staticmethod
    def _bounded(text: str, limit: int) -> str:
        redacted = redact_text(text)
        return (
            redacted
            if len(redacted) <= limit
            else redacted[:limit] + "\n... output truncated ...\n"
        )

    def _run_matrix(
        self,
        name: str,
        matrix: Sequence[Sequence[str]],
        *,
        limit: int,
        steps: list[StepEvidence],
    ) -> tuple[int, int]:
        findings = 0
        infrastructure = 0
        for index, configured in enumerate(matrix, 1):
            argv = [
                sys.executable if item == "@python" else item for item in configured
            ]
            result = self.run(argv, capture=True, check=False)
            classification = (
                "pass"
                if result.returncode == 0
                else (
                    "infrastructure"
                    if result.returncode in {2, 126, 127}
                    else "finding"
                )
            )
            findings += int(classification == "finding")
            infrastructure += int(classification == "infrastructure")
            steps.append(
                StepEvidence(
                    f"{name}:{index}",
                    tuple(argv),
                    result.returncode,
                    classification,
                    True,
                    stdout=self._bounded(result.stdout, limit),
                    stderr=self._bounded(result.stderr, limit),
                )
            )
        return findings, infrastructure

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
        print(
            json.dumps(
                {
                    "schema": "l9.repo-change-policy/v1",
                    "changed_files": list(resolution.files),
                    "selected_gates": [
                        gate.gate_id for gate in select_gates(policy, resolution.files)
                    ],
                    "companion_findings": [finding.rule_id for finding in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
        if findings:
            raise AgentCheckFailure(
                f"change policy found {len(findings)} blocking companion finding(s)"
            )

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
        initial = self.worktree_fingerprint()
        steps: list[StepEvidence] = []
        findings = len(companion_findings(policy["change_policy"], resolution.files))
        infrastructure = 0
        try:
            self.structural_validate()
            steps.append(
                StepEvidence("core-structural-validate", tuple(), 0, "pass", True)
            )
        except WorkflowError as error:
            infrastructure += 1
            steps.append(
                StepEvidence(
                    "core-structural-validate",
                    tuple(),
                    2,
                    "infrastructure",
                    True,
                    stderr=str(error),
                )
            )
        limit = policy["reporting"]["capture_limit_chars"]
        for gate in select_gates(policy["change_policy"], resolution.files):
            found, infra = self._run_matrix(
                f"change-gate:{gate.gate_id}", gate.commands, limit=limit, steps=steps
            )
            findings += found if gate.blocking else 0
            infrastructure += infra if gate.blocking else 0
        for phase in ("validate", "check", "test"):
            result = self.run(["make", phase], capture=True, check=False)
            classification = (
                "pass"
                if result.returncode == 0
                else ("infrastructure" if result.returncode == 2 else "finding")
            )
            findings += int(classification == "finding")
            infrastructure += int(classification == "infrastructure")
            steps.append(
                StepEvidence(
                    f"make:{phase}",
                    ("make", phase),
                    result.returncode,
                    classification,
                    True,
                    stdout=self._bounded(result.stdout, limit),
                    stderr=self._bounded(result.stderr, limit),
                )
            )
        if self.worktree_fingerprint() != initial:
            infrastructure += 1
            steps.append(
                StepEvidence(
                    "non-mutation-check",
                    tuple(),
                    2,
                    "infrastructure",
                    True,
                    stderr="worktree content changed during agent-check",
                )
            )
        else:
            steps.append(StepEvidence("non-mutation-check", tuple(), 0, "pass", True))
        code = 2 if infrastructure else (1 if findings else 0)
        write_reports(
            self.root / policy["reporting"]["agent_check_json"],
            self.root / policy["reporting"]["agent_check_markdown"],
            files=resolution.files,
            change_source=resolution.source,
            base_ref=resolution.base_ref,
            head_ref=resolution.head_ref,
            findings=[],
            steps=steps,
            overall_exit_code=code,
            subject_sha=self.git("rev-parse", "HEAD", capture=True).stdout.strip(),
            policy_sha256=hashlib.sha256(
                (self.root / CORE_POLICY_PATH).read_bytes()
            ).hexdigest(),
        )
        if code == 2:
            raise WorkflowError(
                "agent-check encountered infrastructure/configuration failures"
            )
        if code == 1:
            raise AgentCheckFailure("agent-check found blocking findings")
        print("agent-check: PASS")

    def worktree_fingerprint(self) -> str:
        digest = hashlib.sha256()
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            _fail("unable to fingerprint worktree")
        digest.update(result.stdout)
        return digest.hexdigest()

    def doctor(self) -> None:
        self._ensure_repository_root()
        missing = [tool for tool in ("git", "make") if not shutil.which(tool)]
        if missing:
            _fail("missing tools: " + ", ".join(missing))
        print("doctor: PASS")

    def clean(self) -> None:
        self._ensure_repository_root()
        for relative in self.policy()["clean_paths"]:
            path = (self.root / relative).resolve()
            if self.root not in path.parents:
                _fail(f"unsafe clean path: {relative}")
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

    def status(self) -> None:
        self._ensure_repository_root()
        policy = self.policy()
        fetch = (
            self.git("fetch", "--prune", "origin", capture=True, check=False)
            if policy["status"]["fetch_remote"]
            else None
        )
        print(
            json.dumps(
                {
                    "branch": self.git(
                        "branch", "--show-current", capture=True
                    ).stdout.strip(),
                    "sha": self.git("rev-parse", "HEAD", capture=True).stdout.strip(),
                    "dirty": bool(
                        self.git("status", "--porcelain", capture=True).stdout.strip()
                    ),
                    "remote_freshness": "fresh"
                    if fetch is not None and fetch.returncode == 0
                    else "unknown_offline",
                },
                indent=2,
                sort_keys=True,
            )
        )

    def reconcile(self) -> None:
        self._ensure_repository_root()
        path, stale_after = self._lock_settings()
        try:
            with single_flight(path, stale_after=stale_after):
                super().reconcile()
        except LockBusy as error:
            _fail(str(error))


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
    generic = RepositoryWorkflow(arguments.workspace)
    core = CoreRepositoryWorkflow(arguments.workspace)
    try:
        if arguments.command in {"init", "migrate-v1"}:
            getattr(generic, arguments.command.replace("-", "_"))()
        elif arguments.command in {"validate", "verify-generated"}:
            getattr(generic, arguments.command.replace("-", "_"))()
        elif arguments.command == "core-validate":
            core.structural_validate()
        elif arguments.command == "help":
            print("Common commands: " + " ".join(COMMANDS))
        elif arguments.command in {"change-policy", "agent-check"}:
            getattr(core, arguments.command.replace("-", "_"))(
                explicit=arguments.changed_file,
                base_ref=arguments.base_ref,
                head_ref=arguments.head_ref,
            )
        else:
            getattr(core, arguments.command)()
    except AgentCheckFailure as error:
        print(str(error), file=sys.stderr)
        return 1
    except (WorkflowError, ChangePolicyError, ContractWiringError) as error:
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
