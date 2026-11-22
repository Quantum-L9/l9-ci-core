#!/usr/bin/env python3
"""Gate logic behind the proposed ``ci_runtime_invariants`` (L9-CI-CORE-001..006).

The six statements live in ``.l9/governance-contract.yaml`` and are Proposed
(``docs/adr/0001-*.md``, ``docs/adr/0002-*.md``). This module holds the
deterministic decisions their witnesses exercise. It observes and refuses; it
admits nothing, retires nothing and reads no canonical finding.

A decision that depends on a Core capability that is not built yet does not
degrade to a pass. ``require_capability`` raises ``MissingCapability`` naming
the exact capability, so a witness that cannot run yet fails closed.

``L9-CI-CORE-004`` and ``L9-CI-CORE-006`` are gate logic only. No function here
is a live canary, a ruleset read or a hosted run observation.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / ".l9" / "governance-contract.yaml"
CONTRACT_FIELD = "ci_runtime_invariants"
GITHUB_DIR = ROOT / ".github"
ORG_CI = GITHUB_DIR / "workflows" / "org-ci.yml"
# Semgrep's every-instance chain left the required check for the nightly scan
# (operator decision 2026-11-21, docs/semgrep-runs-nightly.md).
SCAN = GITHUB_DIR / "workflows" / "central-semgrep.yml"
AGGREGATE_ACTION = GITHUB_DIR / "actions" / "aggregate-required-instances"
CORE_ACTIONS = "Quantum-L9/l9-ci-core/.github/actions/"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def contract_invariant_ids(contract: Path = CONTRACT) -> tuple[str, ...]:
    """Return the invariant IDs the governance contract declares, in order."""
    payload = yaml.safe_load(contract.read_text(encoding="utf-8"))
    entries = payload.get(CONTRACT_FIELD) if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{contract}: {CONTRACT_FIELD} is missing or empty")
    ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{contract}: {CONTRACT_FIELD} entry is not a mapping")
        identifier = entry.get("id")
        statement = entry.get("statement")
        if not isinstance(identifier, str) or not isinstance(statement, str):
            raise ValueError(f"{contract}: entry needs a string id and statement")
        if not statement.strip():
            raise ValueError(f"{contract}: {identifier} has an empty statement")
        ids.append(identifier)
    if len(ids) != len(set(ids)):
        raise ValueError(f"{contract}: duplicate invariant id")
    return tuple(ids)


# ---------------------------------------------------------------------------
# Capabilities a witness may need before it can observe anything.
# ---------------------------------------------------------------------------
class MissingCapability(RuntimeError):
    """A witness needs a Core capability that this revision does not carry."""

    def __init__(self, capability: str, detail: str) -> None:
        super().__init__(f"missing capability {capability}: {detail}")
        self.capability = capability
        self.detail = detail


def _schedules_every_instance(text: str) -> bool:
    """The Semgrep scan plans, runs, aggregates and enforces every instance."""
    return all(
        marker in text
        for marker in (
            f"{CORE_ACTIONS}plan-required-instances@",
            f"{CORE_ACTIONS}aggregate-required-instances@",
            "providers inventory",
            "AGGREGATE_STATUS: ${{ steps.aggregate.outputs.status }}",
        )
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _defines(relative: str, definition: str) -> bool:
    """Whether Core's file at ``relative`` carries ``definition``.

    Read from this checkout's own path, never imported by dotted name: Core's
    ``tools/`` is not a package, and under ``unittest discover --start-directory
    tests`` the name ``tools`` resolves to ``tests/tools``, which would report a
    present capability as missing.
    """
    try:
        return definition in (ROOT / relative).read_text(encoding="utf-8")
    except OSError:
        return False


@dataclass(frozen=True)
class Capability:
    name: str
    detail: str
    present: Callable[[], bool]


CAPABILITIES: dict[str, Capability] = {
    capability.name: capability
    for capability in (
        Capability(
            name="required-instance-aggregate",
            detail=(
                "Core cannot yet aggregate the terminal statuses of every "
                "required language and provider instance "
                "(.github/actions/aggregate-required-instances/aggregate.py)"
            ),
            present=lambda: (
                "def aggregate(" in _read(AGGREGATE_ACTION / "aggregate.py")
            ),
        ),
        Capability(
            name="required-instance-scheduling",
            detail=(
                "central-semgrep.yml (the nightly Semgrep scan) must inventory "
                "the snapshot, plan every SDK-selected language and provider "
                "instance (plan-required-instances), run each, and enforce "
                "aggregate-required-instances at a pinned Core revision"
            ),
            present=lambda: _schedules_every_instance(_read(SCAN)),
        ),
        Capability(
            name="inherited-configuration-digest",
            detail=(
                "the stamp and l9.repo-execution/v2 contracts do not yet accept "
                "centrally resolved configuration bound to a content digest "
                "(tools.l9_repo.inherited_config.resolve)"
            ),
            present=lambda: _defines(
                "tools/l9_repo/inherited_config.py", "def resolve("
            ),
        ),
    )
}


def require_capability(name: str) -> None:
    """Raise ``MissingCapability`` unless the named capability is present."""
    capability = CAPABILITIES.get(name)
    if capability is None:
        raise MissingCapability(name, "unknown capability; nothing can vouch for it")
    if not capability.present():
        raise MissingCapability(capability.name, capability.detail)


# ---------------------------------------------------------------------------
# Shared decision shape.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    """``allowed`` is true only when ``reasons`` is empty."""

    allowed: bool
    reasons: tuple[str, ...]

    @classmethod
    def of(cls, reasons: Iterable[str]) -> Decision:
        collected = tuple(reasons)
        return cls(allowed=not collected, reasons=collected)


def _text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    return value.strip() if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# L9-CI-CORE-004: central promotion precedes consumer retirement.
# ---------------------------------------------------------------------------
CANARY_PASS = "CURSOR_CANARY_PASS"
PROMOTED_COORDINATES = ("core_revision", "sdk_revision", "configuration_digest")


def _unbound_coordinates(
    record: Mapping[str, Any], promoted: Mapping[str, Any]
) -> list[str]:
    """Return the promoted coordinates a proof record does not carry exactly."""
    return [
        key
        for key in PROMOTED_COORDINATES
        if not _text(record, key) or _text(record, key) != _text(promoted, key)
    ]


def retirement_decision(evidence: Mapping[str, Any]) -> Decision:
    """Decide whether a consumer-local control may be retired.

    The promoted successor is one Core revision, one SDK revision and one
    configuration digest. Every proof (the known-bad fixture, the known-good
    fixture, the canary and the parity run) is its own record and must carry
    those same three coordinates. A missing, unknown or differently bound proof
    refuses the retirement, so a result observed on an older Core, SDK or
    configuration cannot be replayed for a newer one. Static configuration
    similarity and a green unit test are not proofs.
    """
    reasons: list[str] = []
    for key in ("core_revision", "sdk_revision", "rollback_core_revision"):
        if not FULL_SHA.fullmatch(_text(evidence, key)):
            reasons.append(f"{key} is not a full 40-character revision")
    if not SHA256.fullmatch(_text(evidence, "configuration_digest")):
        reasons.append("configuration_digest is not a sha256 content digest")

    def proof(name: str, label: str) -> Mapping[str, Any] | None:
        record = evidence.get(name)
        if not isinstance(record, Mapping):
            reasons.append(f"no {label} record")
            return None
        for key in _unbound_coordinates(record, evidence):
            reasons.append(f"{label} {key} is not the promoted {key}")
        return record

    known_bad = proof("known_bad", "known-bad fixture")
    if known_bad is not None and known_bad.get("outcome") != "fail":
        reasons.append("known-bad fixture is not proven to fail in the successor")
    known_good = proof("known_good", "known-good fixture")
    if known_good is not None and known_good.get("outcome") != "pass":
        reasons.append("known-good fixture is not proven to pass in the successor")

    canary = proof("canary", "canary")
    if canary is not None:
        if canary.get("status") != CANARY_PASS:
            reasons.append(f"canary status is not {CANARY_PASS}")
        if not _text(canary, "run_url"):
            reasons.append("canary record names no hosted run")

    parity = proof("parity", "integrated parity")
    if parity is not None:
        if parity.get("integrated") is not True:
            reasons.append("parity was not observed in an integrated Core+SDK run")
        if not FULL_SHA.fullmatch(_text(parity, "consumer_revision")):
            reasons.append("parity names no exact consumer revision")
    return Decision.of(reasons)


# ---------------------------------------------------------------------------
# L9-CI-CORE-005: native privileged scanners keep their trust boundary.
# ---------------------------------------------------------------------------
READ_ONLY_PERMISSIONS = {"contents": "read"}
# Read-only organization token for private SDK and registry reads (AGENTS.md §27),
# and the read-only token for a declared external checkout owned outside
# Quantum-L9 (a fine-grained token covers one resource owner; operator,
# 2026-11-22: Contents read-only on cryptoxdog/IB-Odoo_19). Only the
# external-checkout step receives it, before any consumer code.
READ_ONLY_SECRETS = frozenset({"L9_GITHUB_READ_TOKEN", "L9_EXTERNAL_READ_TOKEN"})
SECRET_REFERENCE = re.compile(
    r"secrets\.([A-Za-z_][A-Za-z0-9_]*)|secrets\[['\"]([^'\"]+)"
)
UNTRUSTED_TRIGGERS = frozenset({"pull_request", "merge_group"})


def _permission_violations(scope: str, permissions: object) -> list[str]:
    if permissions is None:
        return []
    if not isinstance(permissions, Mapping):
        # `permissions: write-all` and `read-all` are scalar grants.
        return [f"{scope}: permissions is the scalar grant {permissions!r}"]
    violations = []
    for name, level in permissions.items():
        if READ_ONLY_PERMISSIONS.get(str(name)) != level:
            violations.append(f"{scope}: grants {name}: {level}")
    return violations


def trust_boundary_violations(workflow_text: str) -> list[str]:
    """Return every way a required workflow leaves the read-only PR boundary.

    Applies to a workflow that an untrusted pull request can trigger. Such a
    workflow may hold ``contents: read`` only and may reference no secret other
    than the read-only organization read token.
    """
    workflow = yaml.safe_load(workflow_text)
    if not isinstance(workflow, Mapping):
        return ["workflow is not a mapping"]
    # PyYAML parses the bare key `on` as boolean True.
    triggers = workflow.get(True, workflow.get("on"))
    declared = set(triggers) if isinstance(triggers, (Mapping, list)) else {triggers}
    if not declared & UNTRUSTED_TRIGGERS:
        return []

    violations: list[str] = []
    if "permissions" not in workflow:
        violations.append("workflow: no top-level permissions, so the default applies")
    violations += _permission_violations("workflow", workflow.get("permissions"))
    jobs = workflow.get("jobs")
    if not isinstance(jobs, Mapping) or not jobs:
        return violations + ["workflow declares no jobs"]
    for name, job in jobs.items():
        if not isinstance(job, Mapping):
            continue
        violations += _permission_violations(f"job {name}", job.get("permissions"))
        # `secrets: inherit` hands every caller secret to a reusable workflow
        # without naming one, so no named reference exists to allowlist.
        if "secrets" in job and not isinstance(job["secrets"], Mapping):
            violations.append(
                f"job {name}: passes secrets as {job['secrets']!r}, not by name"
            )
    referenced = {a or b for a, b in SECRET_REFERENCE.findall(workflow_text)}
    for secret in sorted(referenced - READ_ONLY_SECRETS):
        violations.append(f"workflow references secret {secret}")
    return violations


# ---------------------------------------------------------------------------
# L9-CI-CORE-006: coverage is proved at the actual enforcement boundary.
# ---------------------------------------------------------------------------
def _owner(repository: str) -> str:
    owner, _, name = repository.partition("/")
    return owner if owner and name else ""


def coverage_claim(
    repositories: Iterable[str], evidence: Mapping[str, Mapping[str, Any]]
) -> Decision:
    """Decide whether fleet-wide required-CI coverage may be claimed.

    Every in-scope repository needs its own current record: a ruleset owned by
    that repository's account owner, active enforcement, the targeted branch, a
    recorded bypass policy, the check that ruleset requires, and a successful
    run of that same check observed on the evaluated head. A ruleset under
    another owner covers nothing by implication, and a run of some other,
    failed, cancelled or still-running workflow on the same head is not the
    required check.
    """
    reasons: list[str] = []
    scope = list(repositories)
    if not scope:
        reasons.append("no in-scope repository was named")
    for repository in scope:
        owner = _owner(repository)
        if not owner:
            reasons.append(f"{repository}: not an owner/name repository")
            continue
        record = evidence.get(repository)
        if not isinstance(record, Mapping):
            reasons.append(f"{repository}: no enforcement evidence")
            continue
        ruleset_owner = _text(record, "ruleset_owner")
        if not ruleset_owner:
            reasons.append(f"{repository}: ruleset owner is unknown")
        elif ruleset_owner.lower() != owner.lower():
            reasons.append(
                f"{repository}: ruleset is owned by {ruleset_owner}, which does "
                f"not govern {owner}"
            )
        if not _text(record, "ruleset_id"):
            reasons.append(f"{repository}: ruleset identity is unknown")
        if record.get("enforcement") != "active":
            reasons.append(f"{repository}: ruleset enforcement is not active")
        if not _text(record, "target_branch"):
            reasons.append(f"{repository}: targeted branch is unknown")
        if not isinstance(record.get("bypass_actors"), list):
            reasons.append(f"{repository}: bypass policy is unknown")
        required_check = _text(record, "required_check")
        if not required_check:
            reasons.append(f"{repository}: the ruleset's required check is unknown")
        elif _text(record, "observed_check") != required_check:
            reasons.append(
                f"{repository}: the observed run is not the required check "
                f"{required_check!r}"
            )
        if record.get("observed_conclusion") != "success":
            reasons.append(
                f"{repository}: the observed required check did not conclude success"
            )
        head = _text(record, "evaluated_head")
        observed = _text(record, "observed_run_head")
        if not FULL_SHA.fullmatch(head):
            reasons.append(f"{repository}: evaluated head is unknown")
        elif observed != head:
            reasons.append(
                f"{repository}: required check was not observed on the evaluated head"
            )
        if not _text(record, "observed_run_url"):
            reasons.append(f"{repository}: no hosted run observation")
    return Decision.of(reasons)
