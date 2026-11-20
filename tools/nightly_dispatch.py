#!/usr/bin/env python3
"""Plan-only organization nightly scheduler.

The schedule event records a plan. It does not dispatch a job, check out a
consumer, install a consumer dependency, or run a test. Apply is closed:
requesting it is a visible failure and still dispatches nothing. Unknown
activity, permission, run history, registry, or pagination dispatches nothing.

A fixture result and a plan-mode artifact set ``live_schedule_receipt`` false.
Neither is evidence that GitHub ran the cron or that a deep suite ran.

The live plan reads two committed inputs and no network: Core's projection
of the repository registry (``required-repositories.receipt.json``) and the
admitted deep entrypoints (``.github/nightly/deep-entrypoints.json``). With
apply closed, a dispatch entrypoint is recorded as SKIPPED_APPLY_CLOSED: its
evidence is deliberately not read, which is not an unknown. The credentialed
apply path is ``tools/nightly_apply.py``, behind a switch that defaults closed.

This module is Core runtime code. It does not admit organization policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

NIGHTLY_CRON = "17 7 * * *"
NIGHTLY_HOUR = 7
NIGHTLY_MINUTE = 17
ORGANIZATION = "Quantum-L9"
CORE_REPOSITORY = "Quantum-L9/l9-ci-core"
PLAN_SCHEMA = "l9.nightly-plan/v1"
RECEIPT_NOTE = (
    "A fixture or plan-mode artifact is not a live schedule receipt and "
    "does not prove a GitHub schedule run, a dispatch, or a deep suite."
)
MAX_REPOSITORIES = 500
_IDENTITY = re.compile(r"^[A-Za-z0-9._-]+\Z")
_SHA = re.compile(r"^[0-9a-f]{40}\Z")
_SLOT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
ROOT = Path(__file__).resolve().parents[1]

# Standalone pre-checkout gate. nightly-deep.yml embeds this exact text
# because the SHA check has to run before the suite checkout. admit_runner
# executes this string, then applies history and duplicate evidence the
# pre-checkout script cannot see.
DISPATCH_GATE_PYTHON = """\
import os
import re
import sys


def finish(status, code, heavy):
    print(status)
    print("heavy_jobs=" + heavy)
    print("suite_pass=false")
    raise SystemExit(code)


event = os.environ["EVENT_NAME"]
mode = os.environ.get("MODE", "")
expected = os.environ.get("EXPECTED_SHA", "")
observed = os.environ.get("OBSERVED_SHA", "")
slot = os.environ.get("UTC_SLOT", "")
contract = os.environ.get("DEEP_CONTRACT", "")
key = os.environ.get("DISPATCH_KEY", "")
repository = os.environ.get("REPOSITORY", "")
ref_name = os.environ.get("REF_NAME", "")
default_branch = os.environ.get("DEFAULT_BRANCH", "")

core_main = (
    repository == "Quantum-L9/l9-ci-core"
    and ref_name == default_branch
    and default_branch == "main"
)

if event == "schedule":
    if core_main:
        finish("SELF_SCHEDULE", 0, "1")
    finish("REFUSED", 1, "0")
if event != "workflow_dispatch":
    finish("REFUSED", 1, "0")
if mode in ("", "plan"):
    finish("PLAN_ONLY", 0, "0")
if mode != "apply":
    finish("REFUSED", 1, "0")
if not core_main:
    finish("REFUSED", 1, "0")
if slot == "" or contract == "" or key == "":
    finish("MALFORMED_INPUT", 1, "0")
if re.fullmatch(r"[0-9a-f]{40}", expected) is None:
    finish("MALFORMED_SHA", 1, "0")
if expected != observed:
    finish("STALE_SHA", 1, "0")
finish("APPLY_ADMITTED", 0, "1")
"""

# org-ci.yml embeds this exact text. A pull-request scope failure or an
# explicit full-suite override exits non-zero and selects no suite.
PR_GUARD_PYTHON = """\
import os
import sys


def finish(status, code):
    print(status)
    print("heavy=false")
    print("suite_pass=false")
    print("selected_suite=none")
    raise SystemExit(code)


event = os.environ["GH_EVENT"]
override = os.environ.get("FULL_SUITE_OVERRIDE", "").strip().lower()
scope = os.environ.get("PR_SCOPE_STATUS", "").strip().lower()
pr_events = ("pull_request", "merge_group", "merge")

if event not in pr_events:
    finish("NOT_APPLICABLE", 0)
if override not in ("", "0", "false", "no"):
    finish("POLICY_PR_DEEP_FORBIDDEN", 1)
if scope in ("unknown", "failed", "unresolved", "pr_scope_unknown"):
    finish("PR_SCOPE_UNKNOWN", 1)
finish("NO_FULL_SUITE_FALLBACK", 0)
"""


class FixtureError(ValueError):
    """The caller handed the planner an unusable fixture or flag."""


@dataclass(frozen=True)
class RunnerAdmission:
    status: str
    exit_code: int
    heavy: bool
    suite_pass: bool


@dataclass(frozen=True)
class PrPathDecision:
    status: str
    exit_code: int
    heavy: bool
    suite_pass: bool
    selected_suite: str


def _run_embedded(source: str, env: Mapping[str, str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        [sys.executable, "-c", source],
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stderr.strip():
        raise RuntimeError(completed.stderr.strip())
    lines = [line for line in completed.stdout.splitlines() if line]
    if not lines:
        raise RuntimeError("embedded gate produced no status")
    return completed.returncode, lines[0], completed.stdout


def admit_runner(
    *,
    event: str,
    mode: str,
    expected_sha: str,
    observed_sha: str,
    utc_slot: str,
    deep_contract: str,
    dispatch_key: str,
    repository: str,
    ref_name: str,
    default_branch: str,
    run_history: str | None = None,
    seen_dispatch_keys: frozenset[str] = frozenset(),
) -> RunnerAdmission:
    """Admit or refuse one deep-entrypoint invocation.

    ``suite_pass`` is always false. Matching a SHA does not mean the suite
    passed. Unknown run history and a key already accepted dispatch nothing
    heavy, after the shared script has otherwise admitted apply.
    """
    code, status, _stdout = _run_embedded(
        DISPATCH_GATE_PYTHON,
        {
            "EVENT_NAME": event,
            "MODE": mode,
            "EXPECTED_SHA": expected_sha,
            "OBSERVED_SHA": observed_sha,
            "UTC_SLOT": utc_slot,
            "DEEP_CONTRACT": deep_contract,
            "DISPATCH_KEY": dispatch_key,
            "REPOSITORY": repository,
            "REF_NAME": ref_name,
            "DEFAULT_BRANCH": default_branch,
        },
    )
    if status == "APPLY_ADMITTED" and run_history == "unknown":
        return RunnerAdmission("UNKNOWN_RUN_HISTORY", 1, False, False)
    if status == "APPLY_ADMITTED" and dispatch_key in seen_dispatch_keys:
        return RunnerAdmission("SKIPPED_DUPLICATE", 1, False, False)
    heavy = status in {"SELF_SCHEDULE", "APPLY_ADMITTED"} and code == 0
    return RunnerAdmission(status, code, heavy, False)


def pr_path_decision(
    *,
    event: str,
    full_suite_override: str,
    scope_status: str,
) -> PrPathDecision:
    """Refuse a full-suite pull-request fallback.

    An unresolved scope is ``PR_SCOPE_UNKNOWN`` and selects nothing. An
    explicit full-suite override is ``POLICY_PR_DEEP_FORBIDDEN``. Neither
    path substitutes ``unittest discover`` or ``test-deep``.
    """
    code, status, stdout = _run_embedded(
        PR_GUARD_PYTHON,
        {
            "GH_EVENT": event,
            "FULL_SUITE_OVERRIDE": full_suite_override,
            "PR_SCOPE_STATUS": scope_status,
        },
    )
    if "selected_suite=none" not in stdout:
        raise RuntimeError("pr guard lost the no-suite marker")
    if "discover" in stdout or "test-deep" in stdout:
        raise RuntimeError("pr guard selected a suite")
    return PrPathDecision(status, code, False, False, "none")


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str) or value == "":
        raise FixtureError(f"{label} must be an ISO-8601 UTC timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise FixtureError(f"{label} is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise FixtureError(f"{label} must be UTC")
    return parsed.astimezone(timezone.utc)


def _optional_time(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    return _parse_time(value, label)


def cron_anchor(evaluated: datetime) -> datetime:
    """Most recent 07:17 UTC at or before ``evaluated``."""
    anchor = evaluated.replace(
        hour=NIGHTLY_HOUR,
        minute=NIGHTLY_MINUTE,
        second=0,
        microsecond=0,
    )
    if evaluated < anchor:
        anchor -= timedelta(days=1)
    return anchor


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require(mapping: Mapping[str, object], key: str) -> object:
    if key not in mapping:
        raise FixtureError(f"missing {key}")
    return mapping[key]


def _bool(mapping: Mapping[str, object], key: str) -> bool:
    value = _require(mapping, key)
    if not isinstance(value, bool):
        raise FixtureError(f"{key} must be a boolean")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or value == "" or _IDENTITY.fullmatch(value) is None:
        raise FixtureError(f"{label} is not a repository identity token")
    return value


def _plain(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or value == ""
        or any(char in value for char in "|\n\r")
    ):
        raise FixtureError(f"{label} is empty or contains a reserved character")
    return value


def dispatch_key(
    *,
    repository_id: int,
    slot: str,
    workflow: str,
    contract: str,
) -> str:
    """Bounded idempotency token for one repository, slot, entrypoint and contract.

    The canonical tuple is hashed so the key is a plain token
    (``nd1-`` plus 40 hex). Consumer deep entrypoints refuse a key outside
    ``[A-Za-z0-9._:-]``; the tuple itself carries ``|`` and ``/``.
    """
    canonical = f"{ORGANIZATION}|{repository_id}|{slot}|{workflow}|{contract}"
    return "nd1-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]


def _row(
    *,
    repository: str,
    verdict: str,
    reason: str,
    evidence: str,
    alert: bool,
    expected_sha: str | None = None,
    default_branch: str | None = None,
    key: str | None = None,
) -> dict[str, object]:
    return {
        "repository": repository,
        "verdict": verdict,
        "reason": reason,
        "dispatch": False,
        "heavy_job": False,
        "alert": alert,
        "expected_sha": expected_sha,
        "default_branch": default_branch,
        "dispatch_key": key,
        "evidence": evidence,
    }


def _classify(
    raw: Mapping[str, object],
    *,
    window_start: datetime,
    window_end: datetime,
    slot: str,
    seen: set[str],
) -> dict[str, object]:
    owner = _text(_require(raw, "owner"), "owner")
    name = _text(_require(raw, "name"), "name")
    repository = f"{owner}/{name}"
    if owner != ORGANIZATION:
        return _row(
            repository=repository,
            verdict="SKIPPED_NO_ADMISSION",
            reason="outside_organization",
            evidence="owner",
            alert=False,
        )
    if _bool(raw, "archived") or _bool(raw, "disabled") or _bool(raw, "fork"):
        return _row(
            repository=repository,
            verdict="SKIPPED_NO_ADMISSION",
            reason="archived_disabled_or_fork",
            evidence="repository_metadata",
            alert=False,
        )
    lifecycle = _require(raw, "lifecycle")
    if lifecycle is None or not isinstance(lifecycle, str) or lifecycle == "":
        return _row(
            repository=repository,
            verdict="UNKNOWN",
            reason="lifecycle_unreadable",
            evidence="registry",
            alert=True,
        )
    if lifecycle != "current":
        return _row(
            repository=repository,
            verdict="SKIPPED_NO_ADMISSION",
            reason=f"lifecycle_{lifecycle}",
            evidence="registry",
            alert=False,
        )
    workflow = _require(raw, "deep_workflow")
    if workflow is None:
        return _row(
            repository=repository,
            verdict="SKIPPED_NO_DEEP_ENTRYPOINT",
            reason="no_admitted_deep_workflow",
            evidence="deep_workflow",
            alert=False,
        )
    workflow_text = _plain(workflow, "deep_workflow")
    contract = _plain(_require(raw, "deep_contract"), "deep_contract")
    pushed_at = _optional_time(_require(raw, "pushed_at"), "pushed_at")
    activity = _require(raw, "activity")
    if activity not in {"code_push", "negative", "unknown"}:
        raise FixtureError("activity must be code_push, negative, or unknown")
    reason = _plain(_require(raw, "activity_reason"), "activity_reason")
    if pushed_at is None:
        return _row(
            repository=repository,
            verdict="UNKNOWN_ACTIVITY",
            reason="pushed_at_missing",
            evidence=reason,
            alert=True,
        )
    # A head that differs from the last successful deep run is still a
    # change after the window has passed: a failed or missed nightly does
    # not make the repository inactive.
    changed_since_success = raw.get("changed_since_success") is True
    if not changed_since_success and (
        pushed_at < window_start or pushed_at > window_end
    ):
        return _row(
            repository=repository,
            verdict="SKIPPED_INACTIVE",
            reason="outside_window",
            evidence="pushed_at",
            alert=False,
        )
    if activity == "unknown":
        return _row(
            repository=repository,
            verdict="UNKNOWN_ACTIVITY",
            reason=reason,
            evidence="activity_unknown",
            alert=True,
        )
    if activity == "negative":
        return _row(
            repository=repository,
            verdict="SKIPPED_INACTIVE",
            reason=reason,
            evidence="activity_negative",
            alert=False,
        )
    permission = _require(raw, "permission")
    if permission != "known_allowed":
        return _row(
            repository=repository,
            verdict="UNKNOWN_PERMISSION",
            reason="permission_unknown"
            if permission == "unknown"
            else "permission_denied",
            evidence="permission",
            alert=True,
        )
    if _require(raw, "run_history") != "known":
        return _row(
            repository=repository,
            verdict="UNKNOWN_RUN_HISTORY",
            reason="run_history_unknown",
            evidence="run_history",
            alert=True,
        )
    default_branch = _text(_require(raw, "default_branch"), "default_branch")
    expected = _require(raw, "default_sha")
    if not isinstance(expected, str) or _SHA.fullmatch(expected) is None:
        return _row(
            repository=repository,
            verdict="UNKNOWN_RUN_HISTORY",
            reason="malformed_default_sha",
            evidence="default_sha",
            alert=True,
            default_branch=default_branch,
        )
    repository_id = _require(raw, "repository_id")
    if isinstance(repository_id, bool) or not isinstance(repository_id, int):
        raise FixtureError("repository_id must be an integer")
    if repository_id <= 0:
        raise FixtureError("repository_id must be positive")
    key = dispatch_key(
        repository_id=repository_id,
        slot=slot,
        workflow=workflow_text,
        contract=contract,
    )
    success_sha = _require(raw, "successful_sha")
    success_contract = _require(raw, "successful_contract")
    if success_sha == expected and success_contract == contract:
        return _row(
            repository=repository,
            verdict="SKIPPED_ALREADY_TESTED",
            reason="successful_receipt",
            evidence="run_history",
            alert=False,
            expected_sha=expected,
            default_branch=default_branch,
            key=key,
        )
    existing = _require(raw, "existing_dispatch_keys")
    if not isinstance(existing, list) or not all(
        isinstance(item, str) for item in existing
    ):
        raise FixtureError("existing_dispatch_keys must be a list of strings")
    if key in seen or key in existing:
        return _row(
            repository=repository,
            verdict="SKIPPED_DUPLICATE",
            reason="dispatch_key_already_accepted",
            evidence="dispatch_key",
            alert=False,
            expected_sha=expected,
            default_branch=default_branch,
            key=key,
        )
    seen.add(key)
    return _row(
        repository=repository,
        verdict="ELIGIBLE",
        reason="code_push",
        evidence=reason,
        alert=False,
        expected_sha=expected,
        default_branch=default_branch,
        key=key,
    )


def _prepare_mode(event: str, mode: str) -> tuple[str, bool, str | None]:
    if mode not in {"plan", "apply"}:
        raise FixtureError("mode must be plan or apply")
    if event not in {"schedule", "workflow_dispatch"}:
        raise FixtureError("event must be schedule or workflow_dispatch")
    if event == "schedule":
        return mode, mode != "plan", None
    if mode == "apply":
        return "apply", True, "APPLY_CLOSED"
    return mode, False, None


def _assemble(
    *,
    event: str,
    mode_requested: str,
    mode_forced: bool,
    blocked_reason: str | None,
    evaluated: datetime,
    source: str,
    registry_status: str,
    pagination_status: str,
    metadata_status: str,
    metadata_public_count: int | None,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    anchor = cron_anchor(evaluated)
    counts: dict[str, int] = {}
    for row in rows:
        verdict = row["verdict"]
        if not isinstance(verdict, str):
            raise RuntimeError("verdict is not a string")
        counts[verdict] = counts.get(verdict, 0) + 1
    dispatch_count = sum(1 for row in rows if row["dispatch"] is True)
    heavy_job_count = sum(1 for row in rows if row["heavy_job"] is True)
    eligible_count = sum(1 for row in rows if row["verdict"] == "ELIGIBLE")
    alert = any(row["alert"] is True for row in rows)
    plan: dict[str, object] = {
        "schema": PLAN_SCHEMA,
        "mode": "plan",
        "mode_requested": mode_requested,
        "mode_forced": mode_forced,
        "blocked_reason": blocked_reason,
        "event": event,
        "organization": ORGANIZATION,
        "slot": evaluated.strftime("%Y-%m-%d"),
        "evaluated_at": _iso(evaluated),
        "window_start": _iso(evaluated - timedelta(hours=24)),
        "window_end": _iso(evaluated),
        "cron": NIGHTLY_CRON,
        "cron_anchor": _iso(anchor),
        "lateness_seconds": int((evaluated - anchor).total_seconds()),
        "canonical_policy": False,
        "live_schedule_receipt": False,
        "receipt_note": RECEIPT_NOTE,
        "apply_closed": True,
        "dispatch_count": dispatch_count,
        "heavy_job_count": heavy_job_count,
        "consumer_checkouts": 0,
        "eligible_count": eligible_count,
        "alert": alert,
        "registry_status": registry_status,
        "pagination_status": pagination_status,
        "metadata_status": metadata_status,
        "metadata_public_count": metadata_public_count,
        "selected_commands": [],
        "source": source,
        "counts": dict(sorted(counts.items())),
        "rows": rows,
    }
    return _assert_closed(plan)


def _assert_closed(plan: dict[str, object]) -> dict[str, object]:
    if plan.get("live_schedule_receipt") is not False:
        raise RuntimeError("refusing a fabricated live schedule receipt")
    if plan.get("consumer_checkouts") != 0:
        raise RuntimeError("plan checked out a consumer")
    if plan.get("dispatch_count") != 0 or plan.get("heavy_job_count") != 0:
        raise RuntimeError("plan dispatched a job")
    if plan.get("canonical_policy") is not False:
        raise RuntimeError("plan claimed canonical policy")
    rows = plan["rows"]
    if not isinstance(rows, list):
        raise RuntimeError("rows")
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("row")
        if row.get("dispatch") is not False or row.get("heavy_job") is not False:
            raise RuntimeError("row dispatched a job")
    commands = plan["selected_commands"]
    if commands != []:
        raise RuntimeError("plan selected a command")
    return plan


def _fleet_row(reason: str, evidence: str) -> dict[str, object]:
    return _row(
        repository=f"{ORGANIZATION}/*",
        verdict="UNKNOWN",
        reason=reason,
        evidence=evidence,
        alert=True,
    )


def build_plan(
    fixture: Mapping[str, object],
    *,
    mode: str,
    event: str,
    max_repositories: int = MAX_REPOSITORIES,
) -> dict[str, object]:
    """Classify a fixture. The returned plan never dispatches."""
    mode_requested, mode_forced, blocked = _prepare_mode(event, mode)
    evaluated = _parse_time(_require(fixture, "evaluated_at"), "evaluated_at")
    slot = _require(fixture, "slot")
    if not isinstance(slot, str) or _SLOT.fullmatch(slot) is None:
        raise FixtureError("slot must be YYYY-MM-DD")
    if slot != evaluated.strftime("%Y-%m-%d"):
        raise FixtureError("slot must be the UTC date of evaluated_at")
    registry = _bool(fixture, "registry_readable")
    pagination = _bool(fixture, "pagination_complete")
    if not registry:
        return _assemble(
            event=event,
            mode_requested=mode_requested,
            mode_forced=mode_forced,
            blocked_reason=blocked,
            evaluated=evaluated,
            source="fixture",
            registry_status="unreadable",
            pagination_status="not_applicable",
            metadata_status="fixture",
            metadata_public_count=None,
            rows=[_fleet_row("registry_unreadable", "registry")],
        )
    if not pagination:
        return _assemble(
            event=event,
            mode_requested=mode_requested,
            mode_forced=mode_forced,
            blocked_reason=blocked,
            evaluated=evaluated,
            source="fixture",
            registry_status="readable",
            pagination_status="incomplete",
            metadata_status="fixture",
            metadata_public_count=None,
            rows=[_fleet_row("pagination_incomplete", "pagination")],
        )
    repos = _require(fixture, "repositories")
    if not isinstance(repos, list):
        raise FixtureError("repositories must be a list")
    if len(repos) > max_repositories:
        return _assemble(
            event=event,
            mode_requested=mode_requested,
            mode_forced=mode_forced,
            blocked_reason=blocked,
            evaluated=evaluated,
            source="fixture",
            registry_status="readable",
            pagination_status="complete",
            metadata_status="fixture",
            metadata_public_count=None,
            rows=[_fleet_row("census_bound_exceeded", "fixture")],
        )
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    window_start = evaluated - timedelta(hours=24)
    for index, repo in enumerate(repos):
        if not isinstance(repo, dict):
            raise FixtureError(f"repositories[{index}] must be an object")
        row = _classify(
            repo,
            window_start=window_start,
            window_end=evaluated,
            slot=slot,
            seen=seen,
        )
        rows.append(row)
    return _assemble(
        event=event,
        mode_requested=mode_requested,
        mode_forced=mode_forced,
        blocked_reason=blocked,
        evaluated=evaluated,
        source="fixture",
        registry_status="readable",
        pagination_status="complete",
        metadata_status="fixture",
        metadata_public_count=None,
        rows=rows,
    )


REGISTRY_PROJECTION = (
    ROOT
    / ".github"
    / "actions"
    / "run-repository-verification"
    / "required-repositories.receipt.json"
)
ENTRYPOINTS = ROOT / ".github" / "nightly" / "deep-entrypoints.json"
ENTRYPOINTS_SCHEMA = "l9.nightly-deep-entrypoints/v1"
PROJECTION_SCHEMA = "l9.required-repositories-projection/v1"
SCHEDULING = ("self", "dispatch")


@dataclass(frozen=True)
class RegistryProjection:
    members: tuple[str, ...]
    commit: str


@dataclass(frozen=True)
class DeepEntrypoint:
    repository: str
    workflow: str
    contract: str
    scheduling: str


def _repository_coordinate(value: object, label: str) -> str:
    if not isinstance(value, str) or value.count("/") != 1:
        raise FixtureError(f"{label} must be owner/name")
    owner, name = value.split("/")
    _text(owner, f"{label} owner")
    _text(name, f"{label} name")
    return value


def load_registry_projection(path: Path = REGISTRY_PROJECTION) -> RegistryProjection:
    """Read Core's committed, non-canonical projection of the registry.

    The projection names ``Quantum-L9/.github`` registry members whose
    lifecycle is ``current``, with the source commit and a members digest.
    A digest that does not match its members, or an unreadable file, is an
    unreadable registry.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FixtureError(f"registry projection unreadable: {error}") from error
    if not isinstance(data, dict) or data.get("schema") != PROJECTION_SCHEMA:
        raise FixtureError("registry projection has the wrong schema")
    source = data.get("source")
    commit = source.get("commit") if isinstance(source, dict) else None
    if not isinstance(commit, str) or _SHA.fullmatch(commit) is None:
        raise FixtureError("registry projection has no source commit")
    members = data.get("members")
    if not isinstance(members, list) or not members:
        raise FixtureError("registry projection has no members")
    names = [_repository_coordinate(item, "member") for item in members]
    digest = hashlib.sha256(("\n".join(names) + "\n").encode()).hexdigest()
    if data.get("members_sha256") != digest:
        raise FixtureError("registry projection members_sha256 does not match")
    if len(set(names)) != len(names):
        raise FixtureError("registry projection repeats a member")
    return RegistryProjection(tuple(names), commit)


def load_entrypoints(path: Path = ENTRYPOINTS) -> tuple[DeepEntrypoint, ...]:
    """Read the admitted deep entrypoints (docs/nightly-deep-consumer-contract.md)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FixtureError(f"deep entrypoints unreadable: {error}") from error
    if not isinstance(data, dict) or data.get("schema") != ENTRYPOINTS_SCHEMA:
        raise FixtureError("deep entrypoints have the wrong schema")
    raw = data.get("entrypoints")
    if not isinstance(raw, list):
        raise FixtureError("deep entrypoints must be a list")
    entries: list[DeepEntrypoint] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise FixtureError(f"entrypoints[{index}] must be an object")
        repository = _repository_coordinate(item.get("repository"), "repository")
        if repository in seen:
            raise FixtureError(f"{repository} has two deep entrypoints")
        seen.add(repository)
        workflow = _plain(item.get("workflow"), "workflow")
        if (
            not workflow.startswith(".github/workflows/")
            or not workflow.endswith((".yml", ".yaml"))
            or "/" in workflow[len(".github/workflows/") :]
        ):
            raise FixtureError(f"{repository} workflow is not a workflow file")
        contract = _plain(item.get("contract"), "contract")
        if re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", contract) is None:
            raise FixtureError(f"{repository} contract is not a bounded name")
        scheduling = item.get("scheduling")
        if scheduling not in SCHEDULING:
            raise FixtureError(f"{repository} scheduling must be self or dispatch")
        if (scheduling == "self") != (repository == CORE_REPOSITORY):
            raise FixtureError("only Core schedules its own deep suite")
        entries.append(DeepEntrypoint(repository, workflow, contract, str(scheduling)))
    return tuple(entries)


def plan_from_projection(
    projection: RegistryProjection | None,
    entrypoints: tuple[DeepEntrypoint, ...] | None,
    *,
    evaluated_at: datetime,
    event: str,
    mode: str,
    unreadable: str = "",
) -> dict[str, object]:
    """Live plan from committed inputs. It reads no network and no token.

    Apply is closed, so a dispatch entrypoint is SKIPPED_APPLY_CLOSED: its
    activity, permission and run history are deliberately not read, which is
    not an unknown. An unreadable projection or entrypoint file is.
    """
    mode_requested, mode_forced, blocked = _prepare_mode(event, mode)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() != timedelta(0):
        raise FixtureError("evaluated_at must be UTC")
    evaluated = evaluated_at.astimezone(timezone.utc)
    rows: list[dict[str, object]]
    if projection is None:
        registry_status = "unreadable"
        rows = [_fleet_row("registry_unreadable", unreadable or "projection")]
    elif entrypoints is None:
        registry_status = "projection"
        rows = [_fleet_row("entrypoints_unreadable", unreadable or "entrypoints")]
    else:
        registry_status = "projection"
        evidence = f"projection@{projection.commit[:12]}"
        by_repository = {entry.repository: entry for entry in entrypoints}
        rows = []
        for member in projection.members:
            entry = by_repository.get(member)
            if entry is None:
                rows.append(
                    _row(
                        repository=member,
                        verdict="SKIPPED_NO_DEEP_ENTRYPOINT",
                        reason="no_admitted_deep_workflow",
                        evidence=evidence,
                        alert=False,
                    )
                )
            elif entry.scheduling == "self":
                rows.append(
                    _row(
                        repository=member,
                        verdict="SKIPPED_SELF_SCHEDULED",
                        reason="nightly_deep_receipt_gate",
                        evidence=f"{entry.workflow}@{entry.contract}",
                        alert=False,
                    )
                )
            else:
                rows.append(
                    _row(
                        repository=member,
                        verdict="SKIPPED_APPLY_CLOSED",
                        reason="apply_switch_closed",
                        evidence=f"{entry.workflow}@{entry.contract}",
                        alert=False,
                    )
                )
        members = set(projection.members)
        for entry in entrypoints:
            if entry.repository not in members:
                rows.append(
                    _row(
                        repository=entry.repository,
                        verdict="SKIPPED_NOT_PROJECTED",
                        reason="not_in_registry_projection",
                        evidence=evidence,
                        alert=False,
                    )
                )
    return _assemble(
        event=event,
        mode_requested=mode_requested,
        mode_forced=mode_forced,
        blocked_reason=blocked,
        evaluated=evaluated,
        source="live",
        registry_status=registry_status,
        pagination_status="not_applicable",
        metadata_status="not_read",
        metadata_public_count=None,
        rows=rows,
    )


def live_plan(
    *,
    evaluated_at: datetime,
    event: str,
    mode: str,
    projection_path: Path = REGISTRY_PROJECTION,
    entrypoints_path: Path = ENTRYPOINTS,
) -> dict[str, object]:
    try:
        projection: RegistryProjection | None = load_registry_projection(
            projection_path
        )
    except FixtureError as error:
        return plan_from_projection(
            None,
            None,
            evaluated_at=evaluated_at,
            event=event,
            mode=mode,
            unreadable=str(error),
        )
    try:
        entrypoints: tuple[DeepEntrypoint, ...] | None = load_entrypoints(
            entrypoints_path
        )
    except FixtureError as error:
        return plan_from_projection(
            projection,
            None,
            evaluated_at=evaluated_at,
            event=event,
            mode=mode,
            unreadable=str(error),
        )
    return plan_from_projection(
        projection,
        entrypoints,
        evaluated_at=evaluated_at,
        event=event,
        mode=mode,
    )


def plan_exit_code(plan: Mapping[str, object]) -> int:
    """Non-zero when apply was requested or any row is an unknown."""
    if plan.get("blocked_reason"):
        return 1
    if plan.get("alert") is True:
        return 1
    return 0


def _emit(plan: Mapping[str, object], code: int) -> None:
    line = (
        f"mode={plan['mode']} dispatch_count={plan['dispatch_count']} "
        f"heavy_job_count={plan['heavy_job_count']} "
        f"live_schedule_receipt=false exit={code}"
    )
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.write(RECEIPT_NOTE + "\n")


def _write_plan(plan: Mapping[str, object], output: str | None) -> None:
    encoded = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(encoded, end="")
        return
    Path(output).write_text(encoded, encoding="utf-8")


def _cmd_plan(args: argparse.Namespace) -> int:
    if bool(args.fixture) == bool(args.live):
        print("pass exactly one of --fixture and --live", file=sys.stderr)
        return 2
    if args.evaluated_at and args.fixture:
        print("--evaluated-at cannot override a fixture clock", file=sys.stderr)
        return 2
    try:
        if args.fixture:
            plan = build_plan(
                _load_fixture(Path(args.fixture)),
                mode=args.mode,
                event=args.event,
            )
        else:
            evaluated = (
                _parse_time(args.evaluated_at, "evaluated_at")
                if args.evaluated_at
                else datetime.now(timezone.utc)
            )
            plan = live_plan(evaluated_at=evaluated, event=args.event, mode=args.mode)
    except FixtureError as error:
        print(str(error), file=sys.stderr)
        return 2
    code = plan_exit_code(plan)
    _write_plan(plan, args.output)
    _emit(plan, code)
    return code


def _load_fixture(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FixtureError(f"unreadable fixture: {error}") from error
    if not isinstance(data, dict):
        raise FixtureError("fixture must be a JSON object")
    loaded: dict[str, object] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise FixtureError("fixture keys must be strings")
        loaded[key] = value
    return loaded


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nightly_dispatch")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--mode", required=True)
    plan.add_argument("--event", default="schedule")
    plan.add_argument("--fixture")
    plan.add_argument("--live", action="store_true")
    plan.add_argument("--evaluated-at")
    plan.add_argument("--output")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "plan":
        return _cmd_plan(args)
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
