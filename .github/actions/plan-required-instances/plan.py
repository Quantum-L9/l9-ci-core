#!/usr/bin/env python3
"""Plan every provider/target instance the SDK inventory makes applicable.

Input is the SDK's ``l9.capability-inventory/v1`` record for one repository
snapshot. It is a factual record with no requiredness and no verdict. This
module adds only what Core owns: which planned instances are required. It
never picks a preferred language (L9-CI-CORE-003): each prerequisite the SDK
reports is carried into the plan or accounted for by state.

Per ``provider_prerequisites`` entry:

* ``applicable``      -> an instance. Required when Core resolved the provider
  as required and Core executes that target (``executable-targets.json``);
  otherwise planned and visible but not required.
* ``incomplete`` / ``failed`` -> an instance carrying that prerequisite state,
  with the same requiredness. It can never conclude, so a required one makes
  the aggregate incomplete.
* ``not_applicable``  -> no instance; listed under ``not_planned``.

An inventory that is itself ``incomplete`` or ``failed``, or that is not bound
to the evaluated revision, makes the plan ``unresolved``: no aggregate over it
can pass. A plan with no instance carries the SDK's own reasons as its
evidence of non-applicability, and unsupported languages stay listed.

Only the fields named here are read. The record's shape is the SDK's contract;
a record that does not have it is refused, not repaired.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

INVENTORY_SCHEMA = "l9.capability-inventory/v1"
PLAN_SCHEMA = "l9.core-required-instances/v1"
TARGETS_SCHEMA = "l9.core-executable-targets/v1"
TARGETS_PATH = Path(__file__).resolve().parent / "executable-targets.json"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SAFE_TARGET = re.compile(r"^(language|framework|consumer):[a-z0-9][a-z0-9._+-]{0,63}$")
# A consumer rulepack target is named after its owner (SDK ADR 0021,
# ``consumer:<owner>``), so Core cannot list every one. ``consumer:*`` in the
# executable targets means Core executes any declared consumer rulepack.
WILDCARD_TARGET = re.compile(r"^consumer:\*$")
REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}$")
MAX_INVENTORY_BYTES = 1048576
MAX_REASONS = 32

INVENTORY_STATES = frozenset(
    {"applicable", "not_applicable", "unsupported", "incomplete", "failed"}
)
PREREQUISITE_STATES = frozenset(
    {"applicable", "not_applicable", "incomplete", "failed"}
)
UNRESOLVED_INVENTORY_STATES = frozenset({"incomplete", "failed"})


class PlanningError(RuntimeError):
    """The inventory or an input cannot be trusted, so nothing is planned."""


def load_executable_targets(path: Path = TARGETS_PATH) -> dict[str, frozenset[str]]:
    """The targets Core can execute, per provider. Core data, not consumer data."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != TARGETS_SCHEMA:
        raise PlanningError(f"executable targets schema must be {TARGETS_SCHEMA}")
    providers = payload.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise PlanningError("executable targets names no provider")
    result: dict[str, frozenset[str]] = {}
    for provider, targets in providers.items():
        if not SAFE_COMPONENT.fullmatch(str(provider)) or not isinstance(targets, list):
            raise PlanningError("executable targets entry is malformed")
        for target in targets:
            if not isinstance(target, str) or not (
                SAFE_TARGET.fullmatch(target) or WILDCARD_TARGET.fullmatch(target)
            ):
                raise PlanningError(f"executable target {target!r} is malformed")
        result[str(provider)] = frozenset(targets)
    return result


def is_executable(target: str, executable: frozenset[str]) -> bool:
    """Whether Core executes ``target``: listed exactly, or by its kind's ``*``."""
    kind = target.partition(":")[0]
    return target in executable or f"{kind}:*" in executable


def unwrap(payload: object) -> dict[str, Any]:
    """Return the inventory record from a bare record or the CLI envelope."""
    if not isinstance(payload, dict):
        raise PlanningError("inventory must be an object")
    if "schema" not in payload and "result" in payload:
        if payload.get("ok") is not True:
            raise PlanningError("SDK inventory command did not report ok")
        payload = payload["result"]
        if not isinstance(payload, dict):
            raise PlanningError("SDK inventory result must be an object")
    if payload.get("schema") != INVENTORY_SCHEMA:
        raise PlanningError(f"inventory schema must be {INVENTORY_SCHEMA}")
    return payload


def _reasons(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise PlanningError(f"{what} must be a list of non-empty strings")
    return [item.strip() for item in value[:MAX_REASONS]]


def build_plan(
    inventory: object,
    *,
    repository: str,
    revision: str,
    sdk_revision: str,
    required_providers: frozenset[str],
    executable_targets: dict[str, frozenset[str]],
) -> dict[str, Any]:
    if not REPOSITORY.fullmatch(repository):
        raise PlanningError("repository must be owner/name")
    for name, value in (("revision", revision), ("sdk-revision", sdk_revision)):
        if not FULL_SHA.fullmatch(value):
            raise PlanningError(
                f"{name} must be a full 40-character lowercase revision"
            )
    for name in required_providers:
        if not SAFE_COMPONENT.fullmatch(name):
            raise PlanningError(f"required provider {name!r} is malformed")

    record = unwrap(inventory)
    status = record.get("status")
    if status not in INVENTORY_STATES:
        raise PlanningError(f"inventory status {status!r} is not a known state")
    inventory_reasons = _reasons(record.get("reasons"), "inventory reasons")
    snapshot = record.get("snapshot")
    if not isinstance(snapshot, dict):
        raise PlanningError("inventory snapshot must be an object")
    prerequisites = record.get("provider_prerequisites")
    languages = record.get("languages")
    if not isinstance(prerequisites, list) or not isinstance(languages, list):
        raise PlanningError("inventory prerequisites and languages must be lists")

    unresolved: list[str] = []
    if snapshot.get("revision") != revision:
        unresolved.append(
            "SDK inventory is not bound to the evaluated revision "
            f"(snapshot revision {snapshot.get('revision')!r})"
        )
    if status in UNRESOLVED_INVENTORY_STATES:
        unresolved.append(
            f"SDK inventory status is {status}: " + "; ".join(inventory_reasons)
        )

    instances: list[dict[str, Any]] = []
    not_planned: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in prerequisites:
        if not isinstance(entry, dict):
            raise PlanningError("inventory prerequisite must be an object")
        provider = entry.get("provider_id")
        target = entry.get("target")
        state = entry.get("state")
        why = entry.get("reason")
        if not isinstance(provider, str) or not SAFE_COMPONENT.fullmatch(provider):
            raise PlanningError("inventory prerequisite provider_id is malformed")
        if not isinstance(target, str) or not SAFE_TARGET.fullmatch(target):
            raise PlanningError("inventory prerequisite target is malformed")
        if state not in PREREQUISITE_STATES:
            raise PlanningError(f"inventory prerequisite state {state!r} is unknown")
        if not isinstance(why, str) or not why.strip():
            raise PlanningError("inventory prerequisite carries no reason")
        identifier = f"{provider}:{target}"
        if identifier in seen:
            raise PlanningError(f"inventory lists {identifier} more than once")
        seen.add(identifier)
        if state == "not_applicable":
            not_planned.append({"id": identifier, "reason": why.strip()})
            continue
        executable = is_executable(
            target, executable_targets.get(provider, frozenset())
        )
        required = provider in required_providers and executable
        if provider in required_providers and not executable:
            requiredness = "not required: Core does not execute this target"
        elif required:
            requiredness = "required by central governance"
        else:
            requiredness = "not required by central governance for this profile"
        instances.append(
            {
                "provider": provider,
                "target": target,
                "required": required,
                "prerequisite": state,
                "requiredness": requiredness,
                "reason": why.strip(),
            }
        )

    unsupported: list[str] = []
    for entry in languages:
        if not isinstance(entry, dict) or not isinstance(entry.get("language"), str):
            raise PlanningError("inventory language entry is malformed")
        if entry.get("state") == "unsupported":
            unsupported.append(entry["language"])

    plan: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "repository": repository,
        "revision": revision,
        "sdk_revision": sdk_revision,
        "inventory": {
            "schema": INVENTORY_SCHEMA,
            "status": status,
            "snapshot_id": snapshot.get("snapshot_id"),
            "snapshot_dirty": snapshot.get("dirty"),
        },
        "instances": sorted(
            instances, key=lambda item: (item["provider"], item["target"])
        ),
        "not_planned": sorted(not_planned, key=lambda item: item["id"]),
        "unsupported_languages": sorted(unsupported),
    }
    if unresolved:
        plan["unresolved"] = unresolved
    if not instances:
        # The SDK's own reasons are the evidence that nothing applies. An
        # unresolved plan still cannot pass: `unresolved` gates the aggregate.
        plan["not_applicable"] = {"reason": "; ".join(inventory_reasons)}
    return plan


# ---------------------------------------------------------------------------
# Composite-action entrypoint.
# ---------------------------------------------------------------------------
def _workspace_path(value: str, what: str) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    if not value.strip():
        raise PlanningError(f"{what} is required")
    path = (workspace / value).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise PlanningError(f"{what} must remain inside GITHUB_WORKSPACE") from error
    return path


def emit(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    env = os.environ
    try:
        source = _workspace_path(env.get("L9_PLAN_INVENTORY", ""), "inventory")
        output = _workspace_path(env.get("L9_PLAN_OUTPUT", ""), "output")
        if output.exists():
            raise PlanningError("output already exists; a plan is written once")
        try:
            if source.stat().st_size > MAX_INVENTORY_BYTES:
                raise PlanningError("inventory exceeds the size bound")
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PlanningError(f"inventory is unreadable: {error}") from error
        required = frozenset(
            item.strip()
            for item in env.get("L9_PLAN_REQUIRED_PROVIDERS", "").split(",")
            if item.strip()
        )
        plan = build_plan(
            payload,
            repository=env.get("L9_PLAN_REPOSITORY", "").strip(),
            revision=env.get("L9_PLAN_REVISION", "").strip(),
            sdk_revision=env.get("L9_PLAN_SDK_REVISION", "").strip(),
            required_providers=required,
            executable_targets=load_executable_targets(),
        )
    except PlanningError as error:
        print(f"plan-required-instances: {error}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    workspace = Path(env.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    emit("plan-path", output.relative_to(workspace).as_posix())
    emit(
        "targets",
        ",".join(f"{item['provider']}:{item['target']}" for item in plan["instances"]),
    )
    emit(
        "required-count", str(sum(1 for item in plan["instances"] if item["required"]))
    )
    for item in plan["instances"]:
        print(
            f"planned {item['provider']}:{item['target']} "
            f"(prerequisite {item['prerequisite']}; {item['requiredness']})"
        )
    for item in plan["not_planned"]:
        print(f"not planned {item['id']}: {item['reason']}")
    for language in plan["unsupported_languages"]:
        print(f"unsupported language (visible, not planned): {language}")
    for reason in plan.get("unresolved", []):
        print(f"::error::plan is unresolved: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
