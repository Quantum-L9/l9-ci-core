#!/usr/bin/env python3
"""Credentialed nightly paths: the fleet apply and Core's own receipt gate.

``tools/nightly_dispatch.py`` plans with no credential. This module holds the
two paths that read GitHub with one.

Apply dispatches each ELIGIBLE repository's admitted deep entrypoint through
``workflow_dispatch``. It is closed unless ``L9_NIGHTLY_APPLY`` is exactly
``open``. Opening it is an operator decision: no workflow in this repository
sets the switch or holds the credential it needs
(docs/nightly-deep-consumer-contract.md). Apply only dispatches; it never
merges, never checks out a consumer and never runs a suite itself.

Every repository goes through ``nightly_dispatch._classify``, so the plan and
apply share one admission:

* an unchanged repository (head already has a successful run under the
  contract) is SKIPPED_ALREADY_TESTED;
* a changed repository is dispatched once, under a key that is unique to the
  repository, UTC slot, entrypoint and contract;
* a key already seen in the entrypoint's run history is SKIPPED_DUPLICATE;
* a permission or history read that fails is UNKNOWN_* and dispatches
  nothing.

The receipt gate (``receipt``) lets Core's scheduled ``nightly-deep.yml`` skip
a suite when main's HEAD already has a successful scheduled deep run. It
fails toward running: an unreadable history runs the suite.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nightly_dispatch as planner  # noqa: E402

APPLY_SCHEMA = "l9.nightly-apply/v1"
APPLY_RESULT_NAME = "nightly-apply.json"
SWITCH_ENV = "L9_NIGHTLY_APPLY"
SWITCH_OPEN = "open"
DISPATCH_TOKEN_ENV = "L9_NIGHTLY_DISPATCH_TOKEN"
RECEIPT_TOKEN_ENV = "L9_RECEIPT_TOKEN"
CORE_DEEP_WORKFLOW = "nightly-deep.yml"
API = "https://api.github.com"

# (method, path-with-query, json body or None) -> (status, parsed json or None)
Transport = Callable[[str, str, Mapping[str, object] | None], tuple[int, object]]


def switch_open(env: Mapping[str, str]) -> bool:
    """Exactly ``open`` opens apply. Anything else, including unset, is closed."""
    return env.get(SWITCH_ENV, "") == SWITCH_OPEN


def http_transport(token: str, allowed_prefix: str) -> Transport:
    """A transport bound to api.github.com and one path prefix."""

    def call(
        method: str, path: str, body: Mapping[str, object] | None
    ) -> tuple[int, object]:
        if not path.startswith(allowed_prefix) or ".." in path:
            raise ValueError(f"refusing a path outside {allowed_prefix}")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "l9-ci-core-nightly",
            },
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=20) as response:
                status = int(getattr(response, "status", 0))
                raw = response.read(5_000_000)
        except urllib.error.HTTPError as error:
            return int(error.code), None
        except OSError:
            # URLError and timeouts are OSError subclasses.
            return 0, None
        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return 0, None

    return call


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so a credentialed call cannot leave api.github.com."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            msg,
            headers,  # type: ignore[arg-type]
            fp,  # type: ignore[arg-type]
        )


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def parse_run_name(title: object) -> tuple[str, str, str, str] | None:
    """``deep <mode> <slot> <contract> <key>`` -> (mode, slot, contract, key)."""
    if not isinstance(title, str):
        return None
    parts = title.split()
    if len(parts) != 5 or parts[0] != "deep":
        return None
    return parts[1], parts[2], parts[3], parts[4]


def _fresh_evidence(entry: planner.DeepEntrypoint) -> dict[str, object]:
    owner, name = entry.repository.split("/")
    return {
        "owner": owner,
        "name": name,
        "archived": False,
        "disabled": False,
        "fork": False,
        "lifecycle": "current",
        "deep_workflow": entry.workflow,
        "deep_contract": entry.contract,
        "pushed_at": None,
        "activity": "unknown",
        "activity_reason": "unread",
        "permission": "unknown",
        "run_history": "unknown",
        "default_branch": "unknown",
        "default_sha": None,
        "repository_id": 1,
        "successful_sha": None,
        "successful_contract": None,
        "existing_dispatch_keys": [],
    }


def _read_repository(raw: dict[str, object], base: str, transport: Transport) -> str:
    """Fill repository fields; return a failure reason or ``""``."""
    status, repo = transport("GET", base, None)
    if status in {403, 404}:
        raw["permission"] = "denied"
    if status != 200 or not isinstance(repo, dict):
        return f"repository_read:{status}"
    raw["archived"] = repo.get("archived") is True
    raw["disabled"] = repo.get("disabled") is True
    raw["fork"] = repo.get("fork") is True
    branch = repo.get("default_branch")
    repository_id = repo.get("id")
    if not isinstance(branch, str) or type(repository_id) is not int:
        return "repository_fields"
    raw["default_branch"] = branch
    raw["repository_id"] = repository_id
    return ""


def _read_head(raw: dict[str, object], base: str, transport: Transport) -> str:
    """Fill the default-branch head; return a failure reason or ``""``."""
    branch = str(raw["default_branch"])
    status, commit = transport("GET", f"{base}/commits/{_quote(branch)}", None)
    sha = commit.get("sha") if isinstance(commit, dict) else None
    detail = commit.get("commit") if isinstance(commit, dict) else None
    committer = detail.get("committer") if isinstance(detail, dict) else None
    date = committer.get("date") if isinstance(committer, dict) else None
    if status != 200 or not isinstance(sha, str) or not isinstance(date, str):
        return f"head_read:{status}"
    raw["default_sha"] = sha
    raw["pushed_at"] = date
    return ""


def _read_permission(
    raw: dict[str, object], workflow_path: str, transport: Transport
) -> bool:
    """True when the entry point exists and is active."""
    status, workflow = transport("GET", workflow_path, None)
    if status in {403, 404}:
        raw["permission"] = "denied"
        return False
    if isinstance(workflow, dict) and status == 200:
        if workflow.get("state") == "active":
            raw["permission"] = "known_allowed"
            return True
    return False


def history_from_runs(
    runs: list[object], contract: str
) -> tuple[list[str], str | None] | None:
    """Keys seen and the latest successful ``apply`` head, or None if malformed."""
    keys: list[str] = []
    success_sha: str | None = None
    for run in runs:
        if not isinstance(run, dict):
            return None
        parsed = parse_run_name(run.get("display_title"))
        if parsed is None:
            continue
        mode, _slot, run_contract, key = parsed
        keys.append(key)
        head = run.get("head_sha")
        succeeded = run.get("conclusion") == "success" and isinstance(head, str)
        if success_sha is None and mode == "apply" and run_contract == contract:
            success_sha = str(head) if succeeded else None
    return keys, success_sha


def _read_history(
    raw: dict[str, object],
    workflow_path: str,
    contract: str,
    transport: Transport,
) -> None:
    query = urllib.parse.urlencode(
        {
            "event": "workflow_dispatch",
            "branch": str(raw["default_branch"]),
            "per_page": "100",
        }
    )
    status, runs = transport("GET", f"{workflow_path}/runs?{query}", None)
    listed = runs.get("workflow_runs") if isinstance(runs, dict) else None
    history = (
        history_from_runs(listed, contract)
        if status == 200 and isinstance(listed, list)
        else None
    )
    if history is None:
        return
    keys, success_sha = history
    raw["run_history"] = "known"
    raw["existing_dispatch_keys"] = keys
    if success_sha is not None:
        raw["successful_sha"] = success_sha
        raw["successful_contract"] = contract
    raw["changed_since_success"] = success_sha != raw["default_sha"]


def read_evidence(
    entry: planner.DeepEntrypoint,
    transport: Transport,
    *,
    window_start: datetime,
) -> dict[str, object]:
    """Build one ``_classify`` input from REST reads. A failed read is unknown."""
    owner, name = entry.repository.split("/")
    base = f"/repos/{_quote(owner)}/{_quote(name)}"
    raw = _fresh_evidence(entry)
    failure = _read_repository(raw, base, transport) or _read_head(raw, base, transport)
    if failure:
        return _unread_activity(raw, failure)
    workflow_file = entry.workflow.rsplit("/", 1)[-1]
    workflow_path = f"{base}/actions/workflows/{_quote(workflow_file)}"
    if _read_permission(raw, workflow_path, transport):
        _read_history(raw, workflow_path, entry.contract, transport)
    return _known_activity(raw, str(raw["default_sha"]), window_start)


def _unread_activity(raw: dict[str, object], reason: str) -> dict[str, object]:
    # A refused read is reported as the permission it is; any other failed
    # read leaves activity unknown. Both dispatch nothing.
    raw["activity"] = "code_push" if raw["permission"] == "denied" else "unknown"
    raw["activity_reason"] = reason
    raw["pushed_at"] = (
        raw["pushed_at"] or datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()
    )
    raw["changed_since_success"] = True
    return raw


def _known_activity(
    raw: dict[str, object], sha: str, window_start: datetime
) -> dict[str, object]:
    pushed = planner._parse_time(raw["pushed_at"], "pushed_at")
    changed = raw.get("changed_since_success") is True
    if pushed >= window_start or changed:
        raw["activity"] = "code_push"
        raw["activity_reason"] = f"default_branch_head:{sha[:12]}"
    else:
        raw["activity"] = "negative"
        raw["activity_reason"] = "default_branch_unchanged"
    if raw["run_history"] != "known":
        # Without history the planner cannot tell changed from tested.
        raw["changed_since_success"] = True
        raw["activity"] = "code_push"
    return raw


def apply_plan(
    projection: planner.RegistryProjection,
    entrypoints: Sequence[planner.DeepEntrypoint],
    *,
    env: Mapping[str, str],
    transport: Transport | None,
    evaluated_at: datetime,
) -> dict[str, object]:
    """Dispatch ELIGIBLE entrypoints when the switch is open. Never merges."""
    evaluated = evaluated_at.astimezone(timezone.utc)
    slot = evaluated.strftime("%Y-%m-%d")
    result: dict[str, object] = {
        "schema": APPLY_SCHEMA,
        "organization": planner.ORGANIZATION,
        "slot": slot,
        "evaluated_at": evaluated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry_commit": projection.commit,
        "switch": SWITCH_OPEN if switch_open(env) else "closed",
        "status": "APPLY_CLOSED",
        "dispatch_count": 0,
        "alert": False,
        "rows": [],
    }
    if not switch_open(env):
        return result
    if transport is None:
        result["status"] = "UNKNOWN_PERMISSION"
        result["alert"] = True
        result["rows"] = [
            {
                "repository": f"{planner.ORGANIZATION}/*",
                "verdict": "UNKNOWN_PERMISSION",
                "reason": "dispatch_credential_absent",
                "dispatched": False,
            }
        ]
        return result
    window_start = evaluated - timedelta(hours=24)
    members = set(projection.members)
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for entry in entrypoints:
        if entry.scheduling != "dispatch":
            continue
        if entry.repository not in members:
            rows.append(
                {
                    "repository": entry.repository,
                    "verdict": "SKIPPED_NOT_PROJECTED",
                    "reason": "not_in_registry_projection",
                    "dispatched": False,
                    "alert": False,
                }
            )
            continue
        raw = read_evidence(entry, transport, window_start=window_start)
        row = planner._classify(
            raw, window_start=window_start, window_end=evaluated, slot=slot, seen=seen
        )
        out: dict[str, object] = {
            "repository": row["repository"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "expected_sha": row["expected_sha"],
            "dispatch_key": row["dispatch_key"],
            "dispatched": False,
            "alert": row["alert"],
        }
        if row["verdict"] == "ELIGIBLE":
            owner, name = entry.repository.split("/")
            workflow_file = entry.workflow.rsplit("/", 1)[-1]
            status, _ = transport(
                "POST",
                f"/repos/{_quote(owner)}/{_quote(name)}/actions/workflows/"
                f"{_quote(workflow_file)}/dispatches",
                {
                    "ref": row["default_branch"],
                    "inputs": {
                        "expected_sha": row["expected_sha"],
                        "utc_slot": slot,
                        "deep_contract": entry.contract,
                        "dispatch_key": row["dispatch_key"],
                        "mode": "apply",
                    },
                },
            )
            if status in {200, 204}:
                out["verdict"] = "DISPATCHED"
                out["dispatched"] = True
            else:
                out["verdict"] = "DISPATCH_REFUSED"
                out["reason"] = f"dispatch_status:{status}"
                out["alert"] = True
        rows.append(out)
    result["rows"] = rows
    result["dispatch_count"] = sum(1 for row in rows if row["dispatched"] is True)
    result["alert"] = any(row.get("alert") is True for row in rows)
    result["status"] = "APPLIED"
    return result


def apply_exit_code(result: Mapping[str, object]) -> int:
    """Closed apply that was asked for, or any alert, is a visible failure."""
    if result.get("status") == "APPLY_CLOSED" or result.get("alert") is True:
        return 1
    return 0


def receipt_decision(
    transport: Transport | None, *, sha: str, run_id: int
) -> tuple[bool, str]:
    """Core's scheduled deep gate: (run_suite, recorded reason).

    Skip only on proof: a successful scheduled ``nightly-deep.yml`` run for
    exactly ``sha`` other than this one. Anything unreadable runs the suite.
    """
    if planner._SHA.fullmatch(sha) is None:
        return True, "receipt_unknown:malformed_sha"
    if transport is None:
        return True, "receipt_unknown:no_token"
    query = urllib.parse.urlencode(
        {"event": "schedule", "status": "success", "head_sha": sha, "per_page": "20"}
    )
    owner, name = planner.CORE_REPOSITORY.split("/")
    status, body = transport(
        "GET",
        f"/repos/{owner}/{name}/actions/workflows/{CORE_DEEP_WORKFLOW}/runs?{query}",
        None,
    )
    runs = body.get("workflow_runs") if isinstance(body, dict) else None
    if status != 200 or not isinstance(runs, list):
        return True, f"receipt_unknown:history_status:{status}"
    for run in runs:
        if (
            isinstance(run, dict)
            and run.get("head_sha") == sha
            and run.get("conclusion") == "success"
            and run.get("event") == "schedule"
            and run.get("id") != run_id
        ):
            return False, f"successful_receipt:run:{run.get('id')}"
    return True, "no_successful_receipt"


def _cmd_receipt(args: argparse.Namespace) -> int:
    event = os.environ.get("EVENT_NAME", "")
    if event != "schedule":
        run, reason = True, f"not_scheduled:{event or 'unknown'}"
    else:
        token = os.environ.get(RECEIPT_TOKEN_ENV, "")
        prefix = f"/repos/{planner.CORE_REPOSITORY}/actions/"
        transport = http_transport(token, prefix) if token else None
        run, reason = receipt_decision(transport, sha=args.sha, run_id=args.run_id)
    line = f"run={'true' if run else 'false'}\nreason={reason}\n"
    print(line, end="")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        verdict = "runs the suite" if run else "skips the suite"
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"nightly-deep {verdict} for {args.sha}: {reason}\n")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    evaluated = (
        planner._parse_time(args.evaluated_at, "evaluated_at")
        if args.evaluated_at
        else datetime.now(timezone.utc)
    )
    try:
        projection = planner.load_registry_projection()
        entrypoints = planner.load_entrypoints()
    except planner.FixtureError as error:
        print(str(error), file=sys.stderr)
        return 2
    token = os.environ.get(DISPATCH_TOKEN_ENV, "")
    transport = (
        http_transport(token, f"/repos/{planner.ORGANIZATION}/") if token else None
    )
    result = apply_plan(
        projection,
        entrypoints,
        env=os.environ,
        transport=transport,
        evaluated_at=evaluated,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    runner_temp = os.environ.get("RUNNER_TEMP", "")
    if runner_temp:
        # A fixed name in the runner's temp directory; no caller-chosen path.
        (Path(runner_temp) / APPLY_RESULT_NAME).write_text(encoded, encoding="utf-8")
    code = apply_exit_code(result)
    print(
        f"status={result['status']} switch={result['switch']} "
        f"dispatch_count={result['dispatch_count']} exit={code}"
    )
    return code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nightly_apply")
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--evaluated-at")
    receipt = sub.add_parser("receipt")
    receipt.add_argument("--sha", required=True)
    receipt.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "apply":
        return _cmd_apply(args)
    return _cmd_receipt(args)


if __name__ == "__main__":
    raise SystemExit(main())
