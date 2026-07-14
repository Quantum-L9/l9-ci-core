"""Gate-registry loader and semantic validation.

Loads ``.github/governance/gate-registry.yaml``, validates it against
``schemas/gate-registry.schema.json`` plus the semantic rules that a JSON
Schema cannot express, and returns typed :class:`GateSpec` objects bound to the
registry's source and semantic digests.

The registry is the authoritative source for gate identity, owner layer,
executor/result adapters, mode, applicable risk tiers, path selectors,
always-run behavior, timeout, cost class, evidence requirement, and lifecycle.
Workflow YAML never redefines these values.

Beyond structural (schema) validity the loader rejects:

* unknown command keys / missing command implementations,
* missing referenced schema files,
* base schema incompatible with the result adapter,
* timeout outside the PR-B 1..15 minute bound,
* a blocking gate with ``evidence_required: false``,
* a ``local_cli`` gate with ``local_cli_compatible: false``,
* a path-scoped gate with no paths and ``always_run: false``,
* a retired gate that still declares active risk tiers,
* a gate id absent from the PR-A bootstrap result schema (bootstrap_v1),
* and any weakening of the four stable bootstrap gates (removal, rename,
  mode downgrade, evidence removal, owner change, base-schema replacement, or
  disabling ``workflow/contracts`` always-run) — the PR-B policy
  self-protection minimum, which prohibits all relaxations outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import schemas
from .digests import policy_digests

__all__ = [
    "RegistryError",
    "GateSpec",
    "GateRegistry",
    "ALLOWED_COMMANDS",
    "REQUIRED_BOOTSTRAP_GATES",
    "load_registry",
    "validate_registry_data",
]

# Allowlisted command keys -> repo-relative CLI implementation. Mirrors the
# executor command registry (PR §10.2); the registry references only the key.
ALLOWED_COMMANDS: dict[str, str] = {
    "validate_action_pins": ".github/scripts/validate_action_pins.py",
    "validate_download_integrity": ".github/scripts/validate_download_integrity.py",
    "validate_ci_dependencies": ".github/scripts/validate_ci_dependencies.py",
    "validate_workflow_contracts": ".github/scripts/validate_workflow_contracts.py",
}

_BOOTSTRAP_BASE_SCHEMA = "schemas/bootstrap-gate-result.schema.json"
_MIN_TIMEOUT, _MAX_TIMEOUT = 1, 15

# The four stable bootstrap gates and the properties PR-B may not weaken.
REQUIRED_BOOTSTRAP_GATES: dict[str, dict[str, Any]] = {
    "workflow/action-pins": {"owner_layer": "l9_policy_runtime", "always_run": False},
    "workflow/download-integrity": {"owner_layer": "l9_policy_runtime", "always_run": False},
    "dependencies/ci-lock": {"owner_layer": "l9_policy_runtime", "always_run": False},
    "workflow/contracts": {"owner_layer": "l9_assurance", "always_run": True},
}


class RegistryError(ValueError):
    """Raised when the gate registry is structurally or semantically invalid."""


@dataclass(frozen=True, slots=True)
class GateSpec:
    gate_id: str
    display_name: str
    description: str
    owner_layer: str
    executor_type: str
    command_key: str
    result_adapter: str
    base_result_schema: str
    canonical_result_schema: str
    mode: str
    risk_tiers: tuple[str, ...]
    always_run: bool
    paths: tuple[str, ...]
    timeout_minutes: int
    cost_class: str
    evidence_required: bool
    local_cli_compatible: bool
    lifecycle_status: str
    warn_since: str | None
    auto_retire_after_days: int | None

    @property
    def is_active(self) -> bool:
        return self.lifecycle_status != "retired"


@dataclass(frozen=True, slots=True)
class GateRegistry:
    id: str
    owner: str
    default_mode: str
    gates: dict[str, GateSpec]
    path: str | None
    source_digest: str | None
    semantic_digest: str | None

    def gate(self, gate_id: str) -> GateSpec:
        return self.gates[gate_id]


def _repo_root_from(path: Path) -> Path:
    # .../.github/governance/gate-registry.yaml -> repo root is parents[2].
    p = path.resolve()
    if len(p.parents) >= 3:
        return p.parents[2]
    return Path.cwd()


def _bootstrap_gate_ids() -> set[str]:
    """Gate ids allowed by the PR-A bootstrap result schema (its enum)."""
    schema = schemas.load_schema("bootstrap-gate-result")
    enum = schema.get("properties", {}).get("gate_id", {}).get("enum")
    if not enum:
        raise RegistryError("bootstrap-gate-result schema does not constrain gate_id to an enum")
    return set(enum)


def validate_registry_data(
    data: Any,
    *,
    root: Path,
    path: str | None = None,
    source_digest: str | None = None,
    semantic_digest: str | None = None,
) -> GateRegistry:
    """Validate parsed registry ``data`` and return a typed :class:`GateRegistry`."""
    errors = schemas.iter_errors("gate-registry", data)
    if errors:
        joined = "; ".join(schemas.format_error(e) for e in errors)
        raise RegistryError(f"gate-registry schema invalid: {joined}")

    reg = data["registry"]
    bootstrap_ids = _bootstrap_gate_ids()
    gates: dict[str, GateSpec] = {}

    for gate_id, g in data["gates"].items():
        ex = g["executor"]
        command_key = ex["command_key"]
        lifecycle = g["lifecycle"]
        risk_tiers = tuple(g["risk_tiers"])
        always_run = bool(g["selection"]["always_run"])
        paths = tuple(g["selection"]["paths"])
        mode = g["mode"]
        evidence_required = bool(g["evidence_required"])
        timeout = int(g["timeout_minutes"])
        status = lifecycle["status"]

        # --- semantic checks a schema cannot express -------------------------
        if command_key not in ALLOWED_COMMANDS:
            raise RegistryError(
                f"{gate_id}: unknown command key {command_key!r} "
                f"(allowed: {sorted(ALLOWED_COMMANDS)})"
            )
        script = root / ALLOWED_COMMANDS[command_key]
        if not script.is_file():
            raise RegistryError(f"{gate_id}: command implementation missing: {script}")
        for schema_field in ("base_result_schema", "canonical_result_schema"):
            ref = root / g[schema_field]
            if not ref.is_file():
                raise RegistryError(f"{gate_id}: referenced schema missing: {g[schema_field]}")
        if g["result_adapter"] == "bootstrap_v1":
            if g["base_result_schema"] != _BOOTSTRAP_BASE_SCHEMA:
                raise RegistryError(
                    f"{gate_id}: bootstrap_v1 requires base_result_schema "
                    f"{_BOOTSTRAP_BASE_SCHEMA!r}, got {g['base_result_schema']!r}"
                )
            if gate_id not in bootstrap_ids:
                raise RegistryError(f"{gate_id}: gate id absent from PR-A bootstrap result schema")
        if not (_MIN_TIMEOUT <= timeout <= _MAX_TIMEOUT):
            raise RegistryError(
                f"{gate_id}: timeout_minutes {timeout} outside [{_MIN_TIMEOUT},{_MAX_TIMEOUT}]"
            )
        if mode == "blocking" and not evidence_required:
            raise RegistryError(f"{gate_id}: blocking gate must require evidence")
        if ex["type"] == "local_cli" and not g["local_cli_compatible"]:
            raise RegistryError(f"{gate_id}: local_cli gate must be local_cli_compatible")
        if not always_run and not paths:
            raise RegistryError(f"{gate_id}: path-scoped gate has no paths and always_run is false")
        if status == "retired" and risk_tiers:
            raise RegistryError(f"{gate_id}: retired gate must not declare active risk tiers")

        gates[gate_id] = GateSpec(
            gate_id=gate_id,
            display_name=g["display_name"],
            description=g["description"],
            owner_layer=g["owner_layer"],
            executor_type=ex["type"],
            command_key=command_key,
            result_adapter=g["result_adapter"],
            base_result_schema=g["base_result_schema"],
            canonical_result_schema=g["canonical_result_schema"],
            mode=mode,
            risk_tiers=risk_tiers,
            always_run=always_run,
            paths=paths,
            timeout_minutes=timeout,
            cost_class=g["cost_class"],
            evidence_required=evidence_required,
            local_cli_compatible=bool(g["local_cli_compatible"]),
            lifecycle_status=status,
            warn_since=lifecycle["warn_since"],
            auto_retire_after_days=lifecycle["auto_retire_after_days"],
        )

    _enforce_bootstrap_invariants(gates)

    return GateRegistry(
        id=reg["id"],
        owner=reg["owner"],
        default_mode=reg["default_mode"],
        gates=gates,
        path=path,
        source_digest=source_digest,
        semantic_digest=semantic_digest,
    )


def _enforce_bootstrap_invariants(gates: dict[str, GateSpec]) -> None:
    """Prohibit any weakening of the four stable bootstrap gates."""
    for gate_id, required in REQUIRED_BOOTSTRAP_GATES.items():
        spec = gates.get(gate_id)
        if spec is None:
            raise RegistryError(f"bootstrap gate {gate_id!r} may not be removed or renamed")
        if spec.mode != "blocking":
            raise RegistryError(
                f"bootstrap gate {gate_id!r} may not be downgraded from blocking "
                f"(got {spec.mode!r})"
            )
        if not spec.evidence_required:
            raise RegistryError(f"bootstrap gate {gate_id!r} must keep evidence_required: true")
        if spec.owner_layer != required["owner_layer"]:
            raise RegistryError(
                f"bootstrap gate {gate_id!r} owner may not change without a "
                f"migration record (expected {required['owner_layer']!r})"
            )
        if (
            spec.result_adapter != "bootstrap_v1"
            or spec.base_result_schema != _BOOTSTRAP_BASE_SCHEMA
        ):
            raise RegistryError(
                f"bootstrap gate {gate_id!r} may not replace its base result schema"
            )
        if required["always_run"] and not spec.always_run:
            raise RegistryError(f"bootstrap gate {gate_id!r} may not disable always-run behavior")


def load_registry(path: str | Path, *, root: str | Path | None = None) -> GateRegistry:
    """Load, digest, and fully validate the gate registry at ``path``."""
    p = Path(path)
    if not p.is_file():
        raise RegistryError(f"gate registry not found: {p}")
    repo_root = Path(root) if root is not None else _repo_root_from(p)
    src, sem, parsed = policy_digests(p)
    return validate_registry_data(
        parsed,
        root=repo_root,
        path=str(path),
        source_digest=src,
        semantic_digest=sem,
    )
