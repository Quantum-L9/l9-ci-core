#!/usr/bin/env python3
"""Account for every required provider/language instance and aggregate once.

Core plans the instances the SDK selected for one evaluated revision and
records one terminal status per executed instance. This module decides only
whether that accounting is complete and what the single aggregate status is
(L9-CI-CORE-003). It reads Core's own plan and instance records. It never opens
an SDK bundle, finding or gate result, and it derives no verdict of its own:
``pass`` and ``fail`` are the statuses the SDK technical gate already returned
for an instance.

Aggregate rule, in order: any required instance that ``fail``ed makes the
aggregate ``fail``; otherwise any required instance without a trusted completed
record makes it ``incomplete``; otherwise it is ``pass``. An empty plan passes
only with explicit evidence of non-applicability. An instance the plan marks
optional never gates: its missing, unfinished or untrusted record stays visible
in the aggregate and changes no verdict.

The step exits 0 after writing the aggregate; the workflow enforces the central
mode on the outputs. It exits 2 when the plan itself is unusable, because then
nothing can be accounted for.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLAN_SCHEMA = "l9.core-required-instances/v1"
RESULT_SCHEMA = "l9.core-instance-result/v1"
AGGREGATE_SCHEMA = "l9.core-instance-aggregate/v1"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# The SDK capability inventory's target grammar: what a provider analyses.
SAFE_TARGET = re.compile(r"^(language|framework):[a-z0-9][a-z0-9._+-]{0,63}$")
APPLICABLE = "applicable"
MAX_REASONS = 32
MAX_RECORD_BYTES = 65536
MAX_INSTANCES = 64

PASS = "pass"
FAIL = "fail"
INCOMPLETE = "incomplete"
# The SDK technical gate's own exit-code convention (org-ci.yml `gate` step).
EXIT_CODES = {PASS: 0, FAIL: 1, INCOMPLETE: 6}
# Terminal statuses Core may record for an instance. Only pass and fail are
# completed executions; every other value is a required instance that did not
# conclude, and an unrecognized value is treated the same way.
COMPLETED = frozenset({PASS, FAIL})


class PlanError(RuntimeError):
    """The plan cannot be trusted, so no instance can be accounted for."""


@dataclass(frozen=True)
class Instance:
    provider: str
    target: str
    required: bool
    # The SDK's prerequisite state for this target. Anything but ``applicable``
    # means the instance cannot conclude, whatever record turns up for it.
    prerequisite: str = APPLICABLE

    @property
    def identifier(self) -> str:
        return f"{self.provider}:{self.target}"


@dataclass(frozen=True)
class Plan:
    repository: str
    revision: str
    sdk_revision: str
    instances: tuple[Instance, ...]
    not_applicable_reason: str
    # Reasons the plan itself is not a complete account (for example an SDK
    # inventory that failed). Any entry makes the aggregate incomplete.
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class Row:
    identifier: str
    required: bool
    status: str
    completed: bool
    reason: str


def _mapping(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanError(f"{what} must be an object")
    return value


def _sha(value: object, what: str) -> str:
    if not isinstance(value, str) or not FULL_SHA.fullmatch(value):
        raise PlanError(f"{what} must be a full 40-character lowercase revision")
    return value


def parse_plan(payload: object) -> Plan:
    plan = _mapping(payload, "plan")
    if plan.get("schema") != PLAN_SCHEMA:
        raise PlanError(f"plan schema must be {PLAN_SCHEMA}")
    repository = plan.get("repository")
    if not isinstance(repository, str) or "/" not in repository:
        raise PlanError("plan repository must be owner/name")
    entries = plan.get("instances")
    if not isinstance(entries, list) or len(entries) > MAX_INSTANCES:
        raise PlanError(f"plan instances must be a list of at most {MAX_INSTANCES}")
    instances: list[Instance] = []
    for entry in entries:
        record = _mapping(entry, "plan instance")
        provider = record.get("provider")
        target = record.get("target")
        if not isinstance(provider, str) or not SAFE_COMPONENT.fullmatch(provider):
            raise PlanError("plan instance provider is not a safe identifier")
        if not isinstance(target, str) or not SAFE_TARGET.fullmatch(target):
            raise PlanError(
                "plan instance target is not language:<name> or framework:<name>"
            )
        if not isinstance(record.get("required"), bool):
            raise PlanError("plan instance required must be true or false")
        prerequisite = record.get("prerequisite", APPLICABLE)
        if not isinstance(prerequisite, str) or not SAFE_COMPONENT.fullmatch(
            prerequisite
        ):
            raise PlanError("plan instance prerequisite is not a safe identifier")
        instances.append(Instance(provider, target, record["required"], prerequisite))
    identifiers = [instance.identifier for instance in instances]
    if len(identifiers) != len(set(identifiers)):
        raise PlanError("plan lists an instance more than once")
    reason = ""
    not_applicable = plan.get("not_applicable")
    if not_applicable is not None:
        evidence = _mapping(not_applicable, "plan not_applicable")
        reason = str(evidence.get("reason") or "").strip()
        if not reason:
            raise PlanError("plan not_applicable needs a reason")
        if instances:
            raise PlanError("plan cannot be not_applicable and list instances")
    unresolved = plan.get("unresolved", [])
    if (
        not isinstance(unresolved, list)
        or len(unresolved) > MAX_REASONS
        or not all(isinstance(item, str) and item.strip() for item in unresolved)
    ):
        raise PlanError("plan unresolved must be a short list of non-empty reasons")
    return Plan(
        repository=repository,
        revision=_sha(plan.get("revision"), "plan revision"),
        sdk_revision=_sha(plan.get("sdk_revision"), "plan sdk_revision"),
        instances=tuple(instances),
        not_applicable_reason=reason,
        unresolved=tuple(unresolved),
    )


def _untrusted(record: object, plan: Plan, planned: set[str]) -> str:
    """Return why a result record cannot be trusted, or '' when it can."""
    if not isinstance(record, dict):
        return "record is not an object"
    if record.get("schema") != RESULT_SCHEMA:
        return f"record schema is not {RESULT_SCHEMA}"
    identifier = f"{record.get('provider')}:{record.get('target')}"
    if identifier not in planned:
        return f"record is for {identifier}, which the plan does not list"
    if record.get("repository") != plan.repository:
        return "record is for another repository"
    if record.get("revision") != plan.revision:
        return "record is not bound to the evaluated revision"
    if record.get("sdk_revision") != plan.sdk_revision:
        return "record is not bound to the provisioned SDK revision"
    if not isinstance(record.get("status"), str):
        return "record carries no terminal status"
    return ""


def aggregate(plan: Plan, records: list[tuple[str, object]]) -> dict[str, Any]:
    """Return the aggregate for ``plan`` given ``(source name, record)`` pairs."""
    planned = {instance.identifier for instance in plan.instances}
    optional = {i.identifier for i in plan.instances if not i.required}
    trusted: dict[str, list[str]] = {}
    untrusted: list[dict[str, Any]] = []
    for source, record in records:
        reason = _untrusted(record, plan, planned)
        if reason:
            # An untrusted record gates unless it can only concern an instance
            # the plan marks optional. One that names a required instance, an
            # unplanned instance or nothing readable could be hiding a required
            # result, so it stays gating.
            claimed = ""
            if isinstance(record, dict):
                claimed = f"{record.get('provider')}:{record.get('target')}"
            untrusted.append(
                {"source": source, "reason": reason, "gating": claimed not in optional}
            )
            continue
        assert isinstance(record, dict)
        identifier = f"{record['provider']}:{record['target']}"
        trusted.setdefault(identifier, []).append(record["status"])

    rows: list[Row] = []
    for instance in plan.instances:
        statuses = trusted.get(instance.identifier, [])
        if instance.prerequisite != APPLICABLE:
            rows.append(
                Row(
                    instance.identifier,
                    instance.required,
                    f"prerequisite_{instance.prerequisite}",
                    False,
                    "the SDK reports its prerequisite cannot be met",
                )
            )
        elif not statuses:
            rows.append(
                Row(instance.identifier, instance.required, "missing", False, "")
            )
        elif len(statuses) > 1:
            rows.append(
                Row(
                    instance.identifier,
                    instance.required,
                    "untrusted",
                    False,
                    f"{len(statuses)} records claim this instance",
                )
            )
        else:
            status = statuses[0]
            rows.append(
                Row(
                    instance.identifier,
                    instance.required,
                    status,
                    status in COMPLETED,
                    "" if status in COMPLETED else "not a completed execution",
                )
            )

    required = [row for row in rows if row.required]
    reasons: list[str] = []
    for row in required:
        if not row.completed:
            detail = f" ({row.reason})" if row.reason else ""
            reasons.append(
                f"required instance {row.identifier} is {row.status}{detail}"
            )
    for entry in untrusted:
        if entry["gating"]:
            reasons.append(f"untrusted record {entry['source']}: {entry['reason']}")
    reasons.extend(f"plan is unresolved: {item}" for item in plan.unresolved)
    if not plan.instances and not plan.not_applicable_reason:
        reasons.append(
            "plan lists no instance and gives no evidence of non-applicability"
        )

    accounted = not reasons
    if any(row.status == FAIL for row in required):
        status = FAIL
    elif not accounted:
        status = INCOMPLETE
    else:
        status = PASS
    return {
        "schema": AGGREGATE_SCHEMA,
        "repository": plan.repository,
        "revision": plan.revision,
        "sdk_revision": plan.sdk_revision,
        "status": status,
        "exit_code": EXIT_CODES[status],
        "accounted": accounted,
        "planned": len(plan.instances),
        "completed": sum(1 for row in rows if row.completed),
        "not_applicable_reason": plan.not_applicable_reason,
        "instances": [
            {
                "id": row.identifier,
                "required": row.required,
                "status": row.status,
                "completed": row.completed,
                "reason": row.reason,
            }
            for row in rows
        ],
        "untrusted_records": untrusted,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Composite-action entrypoint.
# ---------------------------------------------------------------------------
def _workspace_path(value: str, what: str) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    if not value.strip():
        raise PlanError(f"{what} is required")
    path = (workspace / value).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise PlanError(f"{what} must remain inside GITHUB_WORKSPACE") from error
    return path


def _load(path: Path) -> object:
    if path.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError("exceeds the record size bound")
    return json.loads(path.read_text(encoding="utf-8"))


def load_records(directory: Path) -> list[tuple[str, object]]:
    """Read every record in ``directory``; an unreadable one stays untrusted."""
    records: list[tuple[str, object]] = []
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("*.json")):
        try:
            records.append((path.name, _load(path)))
        except (OSError, ValueError) as error:
            records.append((path.name, f"unreadable: {error}"))
    return records


def emit(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def summarize(result: dict[str, Any]) -> str:
    lines = [
        "### Required instances",
        "",
        f"- Aggregate: `{result['status']}`",
        f"- Planned: {result['planned']}; completed executions: {result['completed']}",
        "",
        "| Instance | Required | Status |",
        "| --- | --- | --- |",
    ]
    for row in result["instances"]:
        required = "required" if row["required"] else "not required"
        lines.append(f"| {row['id']} | {required} | {row['status']} |")
    if result["not_applicable_reason"]:
        lines += ["", f"Not applicable: {result['not_applicable_reason']}"]
    for reason in result["reasons"]:
        lines.append(f"- {reason}")
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        plan_path = _workspace_path(os.environ.get("L9_INSTANCE_PLAN", ""), "plan")
        results = _workspace_path(
            os.environ.get("L9_INSTANCE_RESULTS", ""), "results-directory"
        )
        output = _workspace_path(os.environ.get("L9_INSTANCE_AGGREGATE", ""), "output")
        if output.exists():
            raise PlanError("output already exists; an aggregate is written once")
        try:
            plan = parse_plan(_load(plan_path))
        except (OSError, ValueError) as error:
            raise PlanError(f"plan is unreadable: {error}") from error
    except PlanError as error:
        print(f"aggregate-required-instances: {error}", file=sys.stderr)
        return 2
    result = aggregate(plan, load_records(results))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    emit("status", result["status"])
    emit("exit-code", str(result["exit_code"]))
    emit("accounted", "true" if result["accounted"] else "false")
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    emit("aggregate-path", output.relative_to(workspace).as_posix())
    summary = summarize(result)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as stream:
            stream.write(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
