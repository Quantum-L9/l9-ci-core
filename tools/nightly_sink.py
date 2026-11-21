#!/usr/bin/env python3
"""The nightly findings sink: code scanning, one rolling issue, a ratchet.

Pull requests are judged on new findings only (CENTRAL-DIFF, row 23), so the
findings a repository already carries need an owned destination. Each night
Core scans every campaign repository's main with no baseline and hands the
results here:

``publish``
    Uploads each SARIF file to the repository's code scanning (the system of
    record). Every run is given ``automationDetails.id`` ``l9-central/<name>/``
    first, one category per instance, so alerts deduplicate across nights and
    close themselves when the code is fixed. Waits for processing to finish; a
    rejected or unfinished upload fails.

``report``
    Reads the repository's open code scanning alerts in the ``l9-central/``
    categories and updates one rolling issue in place (created once, never one
    per night). The issue names the CODEOWNERS owner, the alerts that are new,
    fixed and still open against the previous night, and links each alert. The
    previous night is the snapshot the issue itself carries.

    Ratchet: the open count may not rise night over night. Findings matched
    by a live entry of Core's central finding-waiver ledger
    (``l9.finding-waivers/v1``, the same file pull-request gating reads) are
    excluded from the count, by repository, provider, rule, path and
    fingerprint; an expired entry stops excluding its finding, so the count
    rises. A rise is recorded on the issue and fails the run. The first night
    sets the baseline.

Both subcommands call api.github.com only, under one repository's path, with
the token in ``L9_NIGHTLY_SINK_TOKEN`` (the l9-nightly-dispatch App, minted for
that repository with security-events: write, issues: write and contents:
read). Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nightly_apply  # noqa: E402

TOKEN_ENV = "L9_NIGHTLY_SINK_TOKEN"
CATEGORY_PREFIX = "l9-central/"
ISSUE_TITLE = "Nightly findings (Core central scan)"
ISSUE_MARKER = "<!-- l9-nightly-findings -->"
SNAPSHOT = re.compile(r"<!-- l9-nightly-snapshot (\{[^}]*\}) -->")
SNAPSHOT_SCHEMA = "l9.nightly-findings-snapshot/v1"
LEDGER_SCHEMA = "l9.finding-waivers/v1"
LEDGER_KEYS = frozenset(
    {
        "id",
        "owner",
        "reason",
        "created",
        "expires",
        "scope",
        "fingerprint",
        "source_sha256",
    }
)
SCOPE_KEYS = frozenset({"repository", "provider_id", "provider_rule_id", "path"})
REPOSITORY = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
PROCESSING_POLLS = 30
PROCESSING_INTERVAL = 10.0

Transport = nightly_apply.Transport


class SinkError(Exception):
    """A step that cannot conclude: the run fails rather than pass silently."""


# --- publish ------------------------------------------------------------------


def categorised(sarif: Mapping[str, Any], name: str) -> dict[str, Any]:
    """The SARIF with every run in category ``l9-central/<name>/``."""
    if not NAME.match(name):
        raise SinkError(f"invalid instance name {name!r}")
    runs = sarif.get("runs")
    if not isinstance(runs, list) or not runs:
        raise SinkError(f"SARIF for {name} has no runs")
    document = json.loads(json.dumps(sarif))
    for run in document["runs"]:
        details = run.setdefault("automationDetails", {})
        details["id"] = f"{CATEGORY_PREFIX}{name}/"
    return document


def encode(sarif: Mapping[str, Any]) -> str:
    raw = json.dumps(sarif, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def publish(
    transport: Transport,
    repository: str,
    commit: str,
    ref: str,
    files: Mapping[str, Mapping[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, str]]:
    """Upload each named SARIF and wait for code scanning to accept it."""
    results = []
    for name, sarif in sorted(files.items()):
        body = {
            "commit_sha": commit,
            "ref": ref,
            "sarif": encode(categorised(sarif, name)),
        }
        status, response = transport(
            "POST", f"/repos/{repository}/code-scanning/sarifs", body
        )
        if status != 202 or not isinstance(response, dict) or not response.get("id"):
            raise SinkError(f"code scanning refused the {name} upload (HTTP {status})")
        upload = str(response["id"])
        state = _await_processing(transport, repository, upload, sleep)
        results.append({"name": name, "upload": upload, "status": state})
    return results


def retired(
    alerts: Mapping[int, Mapping[str, str]],
    files: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """An empty analysis for every category with open alerts and no file tonight.

    A repository that drops a secondary language or its consumer rulepack
    publishes no SARIF for that instance any more, and code scanning closes an
    alert only when a new analysis of its category omits it. Each such
    category gets an empty run under tonight's tool driver, so its alerts
    close and the issue reports them fixed.
    """
    gone = sorted(
        {
            alert["category"][len(CATEGORY_PREFIX) : -1]
            for alert in alerts.values()
            if alert["category"].endswith("/")
        }
        - set(files)
    )
    if not gone:
        return {}
    first = files[min(files)]
    runs = first.get("runs") or []
    if not runs:
        raise SinkError("tonight's SARIF has no run to take the tool driver from")
    driver = {
        key: value
        for key, value in ((runs[0].get("tool") or {}).get("driver") or {}).items()
        if key in ("name", "informationUri", "version")
    }
    version = first.get("version", "2.1.0")
    return {name: _empty_analysis(version, driver) for name in gone}


def _empty_analysis(version: str, driver: Mapping[str, Any]) -> dict[str, Any]:
    """A fresh SARIF log with one run and no results (never shared)."""
    return {
        "version": version,
        "runs": [{"tool": {"driver": {**driver, "rules": []}}, "results": []}],
    }


def _await_processing(
    transport: Transport,
    repository: str,
    upload: str,
    sleep: Callable[[float], None],
) -> str:
    for _ in range(PROCESSING_POLLS):
        status, response = transport(
            "GET", f"/repos/{repository}/code-scanning/sarifs/{upload}", None
        )
        state = (
            response.get("processing_status") if isinstance(response, dict) else None
        )
        if status == 200 and state == "complete":
            return "complete"
        if status == 200 and state == "failed":
            errors = response.get("errors") if isinstance(response, dict) else None
            raise SinkError(f"code scanning rejected upload {upload}: {errors}")
        sleep(PROCESSING_INTERVAL)
    raise SinkError(f"code scanning did not finish processing upload {upload}")


# --- report -------------------------------------------------------------------


def open_alerts(transport: Transport, repository: str) -> dict[int, dict[str, str]]:
    """Open alerts in the l9-central categories, by alert number."""
    alerts: dict[int, dict[str, str]] = {}
    page = 1
    while True:
        status, response = transport(
            "GET",
            f"/repos/{repository}/code-scanning/alerts"
            f"?state=open&per_page=100&page={page}",
            None,
        )
        if status != 200 or not isinstance(response, list):
            raise SinkError(f"cannot read code scanning alerts (HTTP {status})")
        for alert in response:
            record = _alert_record(alert)
            if record["category"].startswith(CATEGORY_PREFIX):
                alerts[int(alert["number"])] = record
        if len(response) < 100:
            return alerts
        page += 1


def _alert_record(alert: Mapping[str, Any]) -> dict[str, str]:
    instance = alert.get("most_recent_instance") or {}
    where = instance.get("location") or {}
    rule = alert.get("rule") or {}
    severity = rule.get("security_severity_level") or rule.get("severity") or ""
    return {
        "category": str(instance.get("category") or ""),
        "line": str(where.get("start_line") or ""),
        "rule": str(rule.get("id") or rule.get("name") or "?"),
        "severity": str(severity),
        "path": str(where.get("path") or ""),
        "url": str(alert.get("html_url") or ""),
    }


def codeowners(transport: Transport, repository: str, ref: str) -> list[str]:
    """Owners of the repository-wide ``*`` rule; empty when there is none."""
    for path in CODEOWNERS_PATHS:
        status, response = transport(
            "GET", f"/repos/{repository}/contents/{path}?ref={ref}", None
        )
        if status == 404:
            continue
        if status != 200 or not isinstance(response, dict):
            raise SinkError(f"cannot read {path} (HTTP {status})")
        text = base64.b64decode(str(response.get("content", ""))).decode(
            "utf-8", "replace"
        )
        owners: list[str] = []
        for line in text.splitlines():
            fields = line.split("#", 1)[0].split()
            if fields and fields[0] in ("*", "/*", "/**"):
                owners = fields[1:]
        return owners
    return []


def load_ledger(path: Path, repository: str, today: date) -> list[dict[str, Any]]:
    """The central ledger's entries for ``repository`` that are in force today.

    The ledger is Core's ``l9.finding-waivers/v1`` (resolve-governance), whose
    semantics the SDK owns (``l9_ci.policy.waivers``). It is read with the
    SDK's shape: a malformed ledger fails closed, and an entry not yet in force
    or past its expiry excludes nothing.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SinkError(f"finding-waiver ledger unreadable: {error}") from error
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "waivers"}
        or data["schema"] != LEDGER_SCHEMA
        or not isinstance(data["waivers"], list)
    ):
        raise SinkError(f"{path} is not a {LEDGER_SCHEMA} ledger")
    live = []
    for index, entry in enumerate(data["waivers"]):
        scope = entry.get("scope") if isinstance(entry, dict) else None
        if (
            not isinstance(entry, dict)
            or set(entry) != LEDGER_KEYS
            or not isinstance(scope, dict)
            or set(scope) != SCOPE_KEYS
            or not all(isinstance(scope[k], str) and scope[k] for k in SCOPE_KEYS)
            or not isinstance(entry["fingerprint"], str)
        ):
            raise SinkError(f"{path}: waiver {index} is malformed")
        try:
            created = date.fromisoformat(str(entry["created"]))
            expires = date.fromisoformat(str(entry["expires"]))
        except ValueError as error:
            raise SinkError(f"{path}: waiver {index} has a bad date") from error
        if scope["repository"] == repository and created <= today <= expires:
            live.append(entry)
    return live


def waived_findings(
    bundles: Mapping[str, Mapping[str, Any]], entries: Sequence[Mapping[str, Any]]
) -> dict[tuple[str, str, str, str], str]:
    """Waiver ids by alert key (category, SARIF rule, path, start line).

    A finding is waived when a live entry names its provider, provider rule,
    primary path and fingerprint. The key is what the SDK's SARIF projection
    gives the alert: ``ruleId`` is the canonical rule id, else the provider's.
    """
    waived: dict[tuple[str, str, str, str], str] = {}
    for name, bundle in bundles.items():
        for finding in bundle.get("findings") or []:
            locations = finding.get("locations") or []
            if not locations:
                continue
            path = str(locations[0].get("normalized_path") or "")
            hit = next(
                (
                    e
                    for e in entries
                    if e["scope"]["provider_id"] == finding.get("provider_id")
                    and e["scope"]["provider_rule_id"]
                    == finding.get("provider_rule_id")
                    and e["scope"]["path"] == path
                    and e["fingerprint"] == finding.get("fingerprint")
                ),
                None,
            )
            if hit is None:
                continue
            rule = finding.get("canonical_rule_id") or finding.get("provider_rule_id")
            line = str(locations[0].get("start_line") or "")
            key = (CATEGORY_PREFIX + name + "/", str(rule), path, line)
            waived[key] = str(hit["id"])
    return waived


def alert_key(alert: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (alert["category"], alert["rule"], alert["path"], alert["line"])


# --- dependency audit (pip-audit) ---------------------------------------------

DEPENDENCY_PROVIDER = "pip-audit"
ADVISORY_URL = "https://osv.dev/vulnerability/"
AUDITED = ("executed_pass", "executed_fail")


def dependency_fingerprint(name: str, version: str, vulnerability: str) -> str:
    """The ledger fingerprint of one known vulnerability in one locked package."""
    text = "\n".join((DEPENDENCY_PROVIDER, name.lower(), version, vulnerability))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dependency_audit(
    python: str, outcome: str, packages: str, report: Path, lock_dir: Path
) -> dict[str, Any]:
    """security.yml's pip-audit step, read back: what it audited and found.

    A repository without a Python project is not applicable. Any other outcome
    than an executed audit with a readable JSON report is incomplete, and an
    incomplete audit fails the night: an unknown set is not an empty one.
    """
    if python != "true":
        return {"status": "not_applicable", "packages": 0, "lock": "", "vulns": {}}
    lock = "uv.lock" if (lock_dir / "uv.lock").is_file() else "requirements.txt"
    audit: dict[str, Any] = {"lock": lock, "packages": 0, "vulns": {}}
    if outcome not in AUDITED or not packages.isdigit():
        return {**audit, "status": "incomplete", "reason": outcome or "no outcome"}
    audit["packages"] = int(packages)
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        dependencies = data["dependencies"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {**audit, "status": "incomplete", "reason": f"no JSON report ({error})"}
    for dependency in dependencies:
        for vuln in dependency.get("vulns") or []:
            name = str(dependency["name"]).lower()
            version = str(dependency["version"])
            key = f"{name}=={version} {vuln['id']}"
            audit["vulns"][key] = {
                "name": name,
                "version": version,
                "id": str(vuln["id"]),
                "aliases": [str(a) for a in vuln.get("aliases") or []],
                "fix_versions": [str(v) for v in vuln.get("fix_versions") or []],
            }
    if outcome == "executed_fail" and not audit["vulns"]:
        reason = "pip-audit failed without reporting a vulnerability"
        return {**audit, "status": "incomplete", "reason": reason}
    return {**audit, "status": "executed"}


def waived_dependencies(
    audit: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """Waiver ids by vulnerability key, from the same central ledger.

    A ledger entry waives a known vulnerability when its scope names provider
    ``pip-audit``, the vulnerability id as the rule, the audited lock as the
    path, and :func:`dependency_fingerprint` of the package, version and id.
    """
    waived: dict[str, str] = {}
    for key, vuln in audit["vulns"].items():
        fingerprint = dependency_fingerprint(vuln["name"], vuln["version"], vuln["id"])
        for entry in entries:
            scope = entry["scope"]
            if (
                scope["provider_id"] == DEPENDENCY_PROVIDER
                and scope["provider_rule_id"] == vuln["id"]
                and scope["path"] == audit["lock"]
                and entry["fingerprint"] == fingerprint
            ):
                waived[key] = str(entry["id"])
                break
    return waived


def judge_dependencies(
    previous: Mapping[str, Any] | None,
    audit: Mapping[str, Any],
    waived: Mapping[str, str],
) -> dict[str, Any]:
    """The known-vulnerability count night over night; a rise fails.

    A snapshot without a dependency record (the first night, or a repository
    that had no Python project) sets the baseline.
    """
    recorded = previous.get("dependencies") if previous else None
    before = set(recorded) if isinstance(recorded, list) else None
    verdict: dict[str, Any] = {
        "status": audit["status"],
        "reason": audit.get("reason", ""),
        "packages": audit["packages"],
        "lock": audit["lock"],
        "previous_open": None if before is None else len(before),
        "waived": dict(sorted(waived.items())),
    }
    if audit["status"] == "not_applicable":
        return {
            **verdict,
            "new": [],
            "fixed": [],
            "open": [],
            "accepted": None,
            "rose": False,
            "ratchet": "pass",
        }
    if audit["status"] == "incomplete":
        kept = sorted(before) if before is not None else None
        return {
            **verdict,
            "new": [],
            "fixed": [],
            "open": [],
            "accepted": kept,
            "rose": False,
            "ratchet": "fail",
        }
    now = set(audit["vulns"]) - set(waived)
    rose = before is not None and len(now) > len(before)
    return {
        **verdict,
        "new": sorted(now - before) if before is not None else [],
        "fixed": sorted(before - now) if before is not None else [],
        "open": sorted(now),
        "accepted": sorted(before) if rose and before is not None else sorted(now),
        "rose": rose,
        "ratchet": "fail" if rose else "pass",
    }


NOT_AUDITED = {"status": "not_applicable", "packages": 0, "lock": "", "vulns": {}}


def snapshot_of(body: str) -> dict[str, Any] | None:
    match = SNAPSHOT.search(body or "")
    if not match:
        return None
    data = json.loads(match.group(1))
    return data if data.get("schema") == SNAPSHOT_SCHEMA else None


def judge(
    previous: Mapping[str, Any] | None,
    current: Mapping[int, Mapping[str, str]],
    waived: Mapping[int, str],
) -> dict[str, Any]:
    """New, fixed, still-open and waived alerts, and the ratchet verdict.

    Waived alerts are open in code scanning but are not counted: the ratchet
    compares tonight's counted alerts with last night's.
    """
    before = set(previous.get("alerts", [])) if previous else set()
    now = set(current) - set(waived)
    previous_open = len(before) if previous else None
    rose = previous_open is not None and len(now) > previous_open
    return {
        "new": sorted(now - before) if previous else [],
        "fixed": sorted(before - now),
        "open": sorted(now),
        # The snapshot the next night compares with: tonight's alerts, unless
        # the ratchet failed, when the last accepted night stays the bar, so
        # re-running or waiting a night cannot clear a rise.
        "accepted": sorted(before) if rose else sorted(now),
        "waived": dict(sorted(waived.items())),
        "previous_open": previous_open,
        "rose": rose,
        "ratchet": "fail" if rose else "pass",
    }


def render(
    repository: str,
    commit: str,
    owners: Sequence[str],
    alerts: Mapping[int, Mapping[str, str]],
    verdict: Mapping[str, Any],
    night: str,
    run_url: str,
    audit: Mapping[str, Any] = NOT_AUDITED,
) -> str:
    owner = " ".join(owners) if owners else "no CODEOWNERS owner for `*`"
    previous = verdict["previous_open"]
    lines = [
        ISSUE_MARKER,
        f"Core's nightly central scan of `{repository}` main (no baseline, whole tree).",
        "Findings live in code scanning; this issue tracks them night over night.",
        "",
        f"- Owner: {owner}",
        f"- Night: {night}, commit `{commit[:12]}`, run {run_url or 'n/a'}",
        f"- Semgrep, open in code scanning: **{len(verdict['open'])}**"
        + ("" if previous is None else f" (previous night {previous})"),
        f"- New: {len(verdict['new'])} · Fixed: {len(verdict['fixed'])}",
        f"- Waived (central finding-waiver ledger, not counted): {len(verdict['waived'])}",
        f"- Ratchet: **{verdict['ratchet']}**"
        + (" (first night sets the baseline)" if previous is None else ""),
        "",
    ]
    dependencies = verdict.get("dependencies")
    if dependencies and dependencies["status"] != "not_applicable":
        lines[7:7] = [
            f"- pip-audit, known vulnerabilities: **{len(dependencies['open'])}**"
            f" in {dependencies['packages']} audited packages"
            + (
                ""
                if dependencies["previous_open"] is None
                else f" (previous night {dependencies['previous_open']})"
            )
        ]
    if verdict["new"]:
        lines += ["### New", ""]
        lines += [_alert_line(n, alerts[n]) for n in verdict["new"]]
        lines.append("")
    if verdict["fixed"]:
        lines += ["### Fixed", ""] + [f"- #{n}" for n in verdict["fixed"]] + [""]
    still = [n for n in verdict["open"] if n not in set(verdict["new"])]
    if still:
        lines += (
            ["### Still open", ""] + [_alert_line(n, alerts[n]) for n in still] + [""]
        )
    if verdict["waived"]:
        lines += ["### Waived", ""]
        lines += [
            _alert_line(n, alerts[n]) + f" — waiver `{waiver}`"
            for n, waiver in verdict["waived"].items()
        ]
        lines.append("")
    if dependencies and dependencies["status"] != "not_applicable":
        lines += _dependency_section(dependencies, audit)
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "night": night,
        "commit": commit,
        "alerts": verdict["accepted"],
    }
    if dependencies and dependencies["accepted"] is not None:
        snapshot["dependencies"] = dependencies["accepted"]
    lines.append(f"<!-- l9-nightly-snapshot {json.dumps(snapshot, sort_keys=True)} -->")
    return "\n".join(lines) + "\n"


def _dependency_section(
    verdict: Mapping[str, Any], audit: Mapping[str, Any]
) -> list[str]:
    lines = ["## pip-audit (full resolved lock)", ""]
    if verdict["status"] == "incomplete":
        return lines + [
            f"- **Incomplete**: {verdict['reason']}. The locked set is unknown, so "
            "the ratchet fails until a night audits it.",
            "",
        ]
    previous = verdict["previous_open"]
    lines += [
        f"- Packages audited: {verdict['packages']} from `{verdict['lock']}`",
        f"- Known vulnerabilities: **{len(verdict['open'])}**"
        + ("" if previous is None else f" (previous night {previous})")
        + f" · New: {len(verdict['new'])} · Fixed: {len(verdict['fixed'])}"
        + f" · Waived: {len(verdict['waived'])}",
        f"- Ratchet: **{verdict['ratchet']}**"
        + (" (first night sets the baseline)" if previous is None else ""),
        "",
    ]
    vulns = audit["vulns"]
    if verdict["open"]:
        lines += ["### Known vulnerabilities", ""]
        lines += [_vuln_line(vulns[key]) for key in verdict["open"]] + [""]
    if verdict["fixed"]:
        lines += ["### Fixed since the previous night", ""]
        lines += [f"- `{key}`" for key in verdict["fixed"]] + [""]
    if verdict["waived"]:
        lines += ["### Waived (central finding-waiver ledger)", ""]
        lines += [
            _vuln_line(vulns[key]) + f" — waiver `{waiver}`"
            for key, waiver in verdict["waived"].items()
        ] + [""]
    return lines


def _vuln_line(vuln: Mapping[str, Any]) -> str:
    aliases = f" ({', '.join(vuln['aliases'])})" if vuln["aliases"] else ""
    fixes = ", ".join(vuln["fix_versions"]) or "none published"
    return (
        f"- `{vuln['name']}=={vuln['version']}` "
        f"[{vuln['id']}]({ADVISORY_URL}{vuln['id']}){aliases}; fixed in: {fixes}"
    )


def _alert_line(number: int, alert: Mapping[str, str]) -> str:
    severity = f" ({alert['severity']})" if alert.get("severity") else ""
    return f"- [#{number}]({alert['url']}) `{alert['rule']}`{severity} in `{alert['path']}`"


def _is_rolling(issue: Mapping[str, Any]) -> bool:
    return "pull_request" not in issue and ISSUE_MARKER in str(issue.get("body") or "")


def find_issue(
    transport: Transport, repository: str, creator: str
) -> dict[str, Any] | None:
    """The rolling issue this App opened, open or closed; the newest wins."""
    page = 1
    found: dict[str, Any] | None = None
    while True:
        status, response = transport(
            "GET",
            f"/repos/{repository}/issues?state=all&creator={nightly_apply._quote(creator)}"
            f"&per_page=100&page={page}",
            None,
        )
        if status != 200 or not isinstance(response, list):
            raise SinkError(f"cannot list issues (HTTP {status})")
        rolling = [i for i in response if _is_rolling(i)]
        if found is not None:
            rolling.append(found)
        if rolling:
            found = max(rolling, key=lambda issue: int(issue["number"]))
        if len(response) < 100:
            return found
        page += 1


def report(
    transport: Transport,
    repository: str,
    commit: str,
    ref: str,
    creator: str,
    ledger: Sequence[Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
    night: str,
    run_url: str,
    audit: Mapping[str, Any] = NOT_AUDITED,
) -> dict[str, Any]:
    alerts = open_alerts(transport, repository)
    by_key = waived_findings(bundles, ledger)
    waived = {
        number: by_key[alert_key(alert)]
        for number, alert in alerts.items()
        if alert_key(alert) in by_key
    }
    owners = codeowners(transport, repository, ref)
    issue = find_issue(transport, repository, creator)
    previous = snapshot_of(str(issue.get("body") or "")) if issue else None
    verdict = judge(previous, alerts, waived)
    dependencies = judge_dependencies(
        previous, audit, waived_dependencies(audit, ledger)
    )
    verdict["dependencies"] = dependencies
    if dependencies["ratchet"] == "fail":
        verdict["ratchet"] = "fail"
    body = render(repository, commit, owners, alerts, verdict, night, run_url, audit)
    if issue is None:
        status, response = transport(
            "POST", f"/repos/{repository}/issues", {"title": ISSUE_TITLE, "body": body}
        )
        if status != 201 or not isinstance(response, dict):
            raise SinkError(f"cannot open the rolling issue (HTTP {status})")
        number = int(response["number"])
    else:
        number = int(issue["number"])
        status, _ = transport(
            "PATCH",
            f"/repos/{repository}/issues/{number}",
            {"title": ISSUE_TITLE, "body": body, "state": "open"},
        )
        if status != 200:
            raise SinkError(f"cannot update issue #{number} (HTTP {status})")
    notes = []
    if verdict["rose"]:
        notes.append(
            f"open Semgrep alerts rose from {verdict['previous_open']} to "
            f"{len(verdict['open'])}"
        )
    if dependencies["rose"]:
        notes.append(
            f"known vulnerabilities (pip-audit) rose from "
            f"{dependencies['previous_open']} to {len(dependencies['open'])}"
        )
    if dependencies["status"] == "incomplete":
        notes.append(f"the dependency audit is incomplete ({dependencies['reason']})")
    if notes:
        note = (
            f"Ratchet failed on {night}: "
            + "; ".join(notes)
            + ". No live entry of the central finding-waiver ledger covers it. "
            f"Run: {run_url or 'n/a'}"
        )
        status, _ = transport(
            "POST", f"/repos/{repository}/issues/{number}/comments", {"body": note}
        )
        if status != 201:
            raise SinkError(
                f"cannot record the ratchet on issue #{number} (HTTP {status})"
            )
    return {"issue": number, **verdict}


# --- the workflow ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = Path(".github/workflows/nightly-sink.yml")
CUT = "      - name: Enforce repository verification\n"
BIOME_STEP = "        name: Run Biome check (blocking)\n"
BIOME_MODE = "          mode: blocking\n"
EVENT_LINE = "          INPUT_EVENT: ${{ inputs.event || '' }}\n"
MATRIX_ID_LINE = "          INPUT_MATRIX_ID: canary-nightly-${{ matrix.slug }}\n"
MATRIX_ID = "nightly-${{ matrix.slug }}"
LEFT_OVER = re.compile(r"\bneeds\.|\bsecrets\.(?!L9_GITHUB_READ_TOKEN\b)")
TIMEOUT = 60
SECURITY = Path(".github/workflows/security.yml")
AUDIT_STEPS = (
    "Detect project languages",
    "Set up Python",
    "Python dependency audit (pip-audit)",
)
AUDIT_REPORT = "${{ runner.temp }}/nightly-pip-audit.json"
AUDIT_ID = "        id: pip_audit\n"
AUDIT_LEFT_OVER = re.compile(r"\b(inputs|needs)\.|github\.(event|sha|repository)\b")

HEADER = """\
# GENERATED by tools/nightly_sink.py render from org-ci.yml. Do not edit:
# change org-ci.yml (or the tool) and run `python3 tools/nightly_sink.py
# render`. tests/tools/test_nightly_sink.py fails while this file and
# org-ci.yml disagree.
#
# The nightly findings sink (X4). Each night every repository declared in
# .l9/promotion-canary.json is checked out at its main head and scanned by
# org-ci.yml's `analyze` steps, verbatim apart from the target identity, as a
# `nightly` event: the whole tree, no baseline. The steps from "Enforce
# repository verification" on are not run; the sink judges the findings, not
# the repository's pull-request gate. Every instance bundle is projected to
# SARIF by the SDK and uploaded to that repository's code scanning (one
# category per instance), and one rolling "Nightly findings" issue is updated
# with the new, fixed and still-open alerts. security.yml's own pip-audit step
# (inlined from security.yml, not copied) audits each repository's full
# resolved lock, and its known vulnerabilities go to the same issue. An open
# count (either tool) that rises, or an incomplete audit, fails
# the run; findings a live entry of Core's central finding-waiver ledger
# (l9.finding-waivers/v1, the file the scan itself was given) matches are not
# counted. Writes use the
# l9-nightly-dispatch App, minted per repository with security-events: write
# and issues: write. Analyze steps digest: @@DIGEST@@
name: Nightly findings sink (Core)
on:
  schedule:
    - cron: "47 8 * * *"
  workflow_dispatch:
    inputs:
      repository:
        description: One declared repository (owner/name); empty runs every one
        required: false
        default: ""
        type: string
permissions:
  contents: read
concurrency:
  group: nightly-sink-${{ github.ref }}
  cancel-in-progress: false
jobs:
  plan:
    name: Plan the nightly sink
    if: github.repository == 'Quantum-L9/l9-ci-core'
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    outputs:
      matrix: ${{ steps.plan.outputs.matrix }}
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.2.0
        with:
          persist-credentials: false
      - id: plan
        name: Resolve each repository's main head
        env:
          L9_NIGHTLY_REPOSITORY: ${{ inputs.repository || '' }}
          L9_CANARY_TOKEN: ${{ github.token }}
          L9_CANARY_PRIVATE_TOKEN: ${{ secrets.L9_GITHUB_READ_TOKEN }}
        run: |
          set -euo pipefail
          python3 -I tools/nightly_sink.py plan

  sink:
    name: Nightly ${{ matrix.repository }}
    needs: plan
    runs-on: ubuntu-24.04
    timeout-minutes: @@TIMEOUT@@
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}
    steps:
"""

SINK = """\
      # --- nightly sink (not part of org-ci.yml) ---
      - id: project_sarif
        name: Project every instance bundle to SARIF (SDK)
        env:
          L9_EXECUTABLE: ${{ steps.sdk.outputs.executable }}
          L9_STRICT: ${{ steps.gov.outputs.strict }}
          L9_PRIMARY_LANGUAGE: ${{ steps.language.outputs.language }}
          MATRIX_ID: @@MATRIX_ID@@
        run: |
          set -euo pipefail
          python3 -I - <<'PY'
          import os
          import re
          import shutil
          import subprocess
          import sys
          from pathlib import Path

          # The primary language's SARIF is the one the analyze steps
          # projected; every other instance (secondary languages and the
          # declared consumer rulepack) has its own bundle, projected here by
          # the same SDK. One file, so one code scanning category, per instance.
          env = os.environ
          runtime = Path(".l9/runtime", env["MATRIX_ID"])
          out = Path(env["RUNNER_TEMP"], "nightly-sarif")
          out.mkdir(parents=True, exist_ok=True)
          kept = Path(env["RUNNER_TEMP"], "nightly-bundles")
          kept.mkdir(parents=True, exist_ok=True)
          name = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
          primary = env.get("L9_PRIMARY_LANGUAGE", "")
          if not name.match(primary) or not (runtime / "results.sarif").is_file():
              sys.exit("::error::the analyze steps produced no primary SARIF")
          shutil.copyfile(runtime / "results.sarif", out / (primary + ".sarif"))
          shutil.copyfile(runtime / "finding-bundle.json", kept / (primary + ".json"))
          print("instance " + primary + ": results.sarif")
          errors = []
          bundles = sorted((runtime / "required-instances").glob("*/finding-bundle.json"))
          for bundle in bundles:
              instance = bundle.parent.name
              if instance == primary or not name.match(instance):
                  errors.append("unexpected instance directory " + instance)
                  continue
              shutil.copyfile(bundle, kept / (instance + ".json"))
              command = [env["L9_EXECUTABLE"], "bundle", "project-sarif",
                         "--input", str(bundle), "--output", str(out / (instance + ".sarif"))]
              if env.get("L9_STRICT") == "true":
                  command.append("--strict")
              code = subprocess.run(command, check=False).returncode
              if code != 0:
                  errors.append("instance " + instance + ": projection exited " + str(code))
              else:
                  print("instance " + instance + ": projected")
          for error in errors:
              print("::error::" + error, file=sys.stderr)
          sys.exit(1 if errors else 0)
          PY

@@AUDIT@@
      - name: Summarize the dependency audit
        if: ${{ !cancelled() }}
        env:
          REPOSITORY: ${{ matrix.repository }}
          PYTHON_PROJECT: ${{ steps.detect.outputs.python }}
          OUTCOME: ${{ steps.pip_audit.outputs.outcome }}
          PACKAGES: ${{ steps.pip_audit.outputs.packages }}
        run: |
          set -euo pipefail
          line="pip-audit ${REPOSITORY}: python=${PYTHON_PROJECT:-?} outcome=${OUTCOME:-none} packages=${PACKAGES:-0}"
          echo "${line}"
          echo "- ${line}" >> "${GITHUB_STEP_SUMMARY}"

      # Each sink step runs only when the one it depends on succeeded, so a
      # failed dependency audit still reaches the issue, while a failed or
      # partial upload never writes a stale state into its snapshot.
      - id: sink_tool
        name: Fetch the sink tool (Core, this run's revision)
        if: ${{ !cancelled() && steps.project_sarif.outcome == 'success' }}
        env:
          REVISION: ${{ github.sha }}
          TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          core="${RUNNER_TEMP}/nightly-core"
          mkdir -p "${core}"
          cd "${core}"
          git init -q .
          git remote add origin "https://github.com/Quantum-L9/l9-ci-core.git"
          auth_header="Authorization: Basic $(printf 'x-access-token:%s' "${TOKEN}" | base64 | tr -d '\\n')"
          git -c protocol.version=2 \\
              -c "http.https://github.com/.extraheader=${auth_header}" \\
              fetch -q --depth=1 origin "${REVISION}"
          git checkout -q --detach FETCH_HEAD
          test "$(git rev-parse HEAD)" = "${REVISION}"

      # Minting fails when the App's installation lacks a requested
      # permission or the repository, so a missing grant stops here.
      - id: sink_token
        name: Mint the sink token for this repository (GitHub App)
        if: ${{ !cancelled() && steps.sink_tool.outcome == 'success' }}
        uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3.3.0
        with:
          app-id: ${{ vars.L9_NIGHTLY_APP_ID }}
          private-key: ${{ secrets.L9_NIGHTLY_APP_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: ${{ matrix.name }}
          permission-security-events: write
          permission-issues: write
          permission-contents: read

      - id: upload_sarif
        name: Upload SARIF to code scanning
        if: ${{ !cancelled() && steps.sink_token.outcome == 'success' }}
        env:
          L9_NIGHTLY_SINK_TOKEN: ${{ steps.sink_token.outputs.token }}
          GITHUB_TOKEN: ""
          GH_TOKEN: ""
          REPOSITORY: ${{ matrix.repository }}
          REVISION: ${{ matrix.revision }}
          BRANCH: ${{ matrix.branch }}
        run: |
          set -euo pipefail
          python3 -I "${RUNNER_TEMP}/nightly-core/tools/nightly_sink.py" publish \\
            --repository "${REPOSITORY}" \\
            --commit "${REVISION}" \\
            --ref "refs/heads/${BRANCH}" \\
            --sarif-dir "${RUNNER_TEMP}/nightly-sarif"

      - name: Update the nightly findings issue and apply the ratchet
        if: ${{ !cancelled() && steps.upload_sarif.outcome == 'success' }}
        env:
          L9_NIGHTLY_SINK_TOKEN: ${{ steps.sink_token.outputs.token }}
          GITHUB_TOKEN: ""
          GH_TOKEN: ""
          REPOSITORY: ${{ matrix.repository }}
          REVISION: ${{ matrix.revision }}
          BRANCH: ${{ matrix.branch }}
          CREATOR: ${{ steps.sink_token.outputs.app-slug }}[bot]
          # The central ledger the scan above was given (resolve-governance).
          L9_FINDING_WAIVERS: ${{ steps.gov.outputs['finding-waivers'] }}
          PYTHON_PROJECT: ${{ steps.detect.outputs.python }}
          PIP_AUDIT_OUTCOME: ${{ steps.pip_audit.outputs.outcome }}
          PIP_AUDIT_PACKAGES: ${{ steps.pip_audit.outputs.packages }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          set -euo pipefail
          core="${RUNNER_TEMP}/nightly-core"
          python3 -I "${core}/tools/nightly_sink.py" report \\
            --repository "${REPOSITORY}" \\
            --commit "${REVISION}" \\
            --ref "refs/heads/${BRANCH}" \\
            --creator "${CREATOR}" \\
            --ledger "${L9_FINDING_WAIVERS}" \\
            --bundle-dir "${RUNNER_TEMP}/nightly-bundles" \\
            --python-project "${PYTHON_PROJECT}" \\
            --pip-audit-outcome "${PIP_AUDIT_OUTCOME}" \\
            --pip-audit-packages "${PIP_AUDIT_PACKAGES}" \\
            --pip-audit-report "${RUNNER_TEMP}/nightly-pip-audit.json" \\
            --lock-dir "${GITHUB_WORKSPACE}" \\
            --run-url "${RUN_URL}"
"""


def _canary() -> Any:
    import promotion_canary  # noqa: PLC0415 — render and plan only

    return promotion_canary


def sink_steps(org_ci: str) -> str:
    """org-ci.yml's analyze steps as a nightly scan of the matrix repository."""
    pc = _canary()
    try:
        steps = pc.retarget(pc.analyze_steps(org_ci), "nightly")
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    for old, new in (
        (EVENT_LINE, "          INPUT_EVENT: nightly\n"),
        (MATRIX_ID_LINE, f"          INPUT_MATRIX_ID: {MATRIX_ID}\n"),
    ):
        if steps.count(old) != 1:
            raise SinkError(
                f"org-ci.yml's analyze steps no longer contain {old.strip()!r} once"
            )
        steps = steps.replace(old, new)
    # The sink judges Semgrep's findings; Biome's own blocking verdict belongs
    # to the pull-request gate, and in blocking mode it would stop the job
    # before the Semgrep scan and the publication.
    if steps.count(BIOME_STEP) != 1:
        raise SinkError(
            f"org-ci.yml's analyze steps no longer contain {BIOME_STEP.strip()!r} once"
        )
    head, _, tail = steps.partition(BIOME_STEP)
    step, separator, rest = tail.partition("\n      - ")
    if step.count(BIOME_MODE) != 1:
        raise SinkError("the Biome check step no longer sets `mode: blocking` once")
    steps = (
        head
        + "        name: Run Biome check (advisory in the nightly sink)\n"
        + step.replace(BIOME_MODE, "          mode: advisory\n")
        + separator
        + rest
    )
    lines = steps.splitlines(keepends=True)
    try:
        cut = lines.index(CUT)
    except ValueError as error:
        raise SinkError(
            f"org-ci.yml's analyze steps have no {CUT.strip()!r} step"
        ) from error
    while cut and (
        not lines[cut - 1].strip() or lines[cut - 1].lstrip().startswith("#")
    ):
        cut -= 1
    kept = "".join(lines[:cut]).rstrip("\n") + "\n"
    left = [
        line.strip()
        for line in kept.splitlines()
        if not line.lstrip().startswith("#") and LEFT_OVER.search(line)
    ]
    if left:
        raise SinkError(
            "references the nightly sink cannot satisfy: " + "; ".join(left)
        )
    return kept


def audit_steps(security: str) -> str:
    """security.yml's dependency audit, as the nightly runs it.

    The detection, Python and pip-audit steps of security.yml's job are taken
    verbatim, never copied by hand, so the nightly audits exactly what the
    pull-request gate audits. Only the event identity is resolved: the nightly
    is not a pull request, merge group or push, so the step audits the full
    resolved lock (row 23), and the ``pip-audit-report`` input asks it for a
    JSON report. With a report path the step ends cleanly on pip-audit's
    "vulnerabilities found" exit, so a vulnerable lock reaches the ratchet;
    any other failure (no lock, an export that fails) fails the step, and the
    sink steps after it still run, so the issue records the incomplete audit
    and the report step fails the night.
    """
    pc = _canary()
    try:
        steps, _, _ = pc._single_job(security, "security.yml")
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    items = re.split(r"(?m)^(?= {6}- )", steps)
    chosen = []
    for name in AUDIT_STEPS:
        found = [
            i
            for i in items
            if re.search(rf"(?m)^ {{6,8}}(- )?name: {re.escape(name)}$", i)
        ]
        if len(found) != 1:
            raise SinkError(f"security.yml no longer has one {name!r} step")
        chosen.append(found[0].rstrip("\n") + "\n")
    text = "".join(chosen)
    defaults = {**pc._inputs(security), "pip-audit-report": AUDIT_REPORT}

    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in defaults:
            raise SinkError(f"security.yml input {key!r} has no value")
        return json.dumps(defaults[key])

    text = re.sub(r"\$\{\{\s*inputs\.([a-z0-9-]+)\s*\}\}", value, text)
    text = text.replace("${{ github.event_name }}", "nightly")
    text = re.sub(r"\$\{\{\s*github\.event\.[a-z_.]+\s*\}\}", '""', text)
    text = text.replace("${{ github.sha }}", "${{ matrix.revision }}")
    if text.count(AUDIT_ID) != 1:
        raise SinkError("security.yml's pip-audit step no longer has id pip_audit")
    left = [
        line.strip()
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and AUDIT_LEFT_OVER.search(line)
    ]
    if left:
        raise SinkError(
            "security.yml references the sink cannot satisfy: " + "; ".join(left)
        )
    return (
        "      # --- security.yml's dependency audit (generated, not copied) ---\n"
        + text
    )


def render_workflow(org_ci: str, security: str) -> str:
    steps = sink_steps(org_ci)
    audit = audit_steps(security)
    digest = hashlib.sha256((steps + audit).encode("utf-8")).hexdigest()
    header = HEADER.replace("@@DIGEST@@", digest).replace("@@TIMEOUT@@", str(TIMEOUT))
    sink = SINK.replace("@@MATRIX_ID@@", MATRIX_ID).replace("@@AUDIT@@\n", audit + "\n")
    return header + steps + "\n" + sink


def plan_matrix(root: Path, only: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Each declared repository's main head; ``only`` narrows it to one."""
    pc = _canary()
    try:
        entries = pc.load_declaration(root)
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    if only:
        entries = [e for e in entries if e["repository"].lower() == only.lower()]
        if not entries:
            raise SinkError(f"{only} is not declared in {pc.DECLARATION}")
    include = []
    for entry in entries:
        try:
            leg = pc.resolve(entry, dict(env))
        except pc.CanaryError as error:
            raise SinkError(str(error)) from error
        leg["name"] = leg["repository"].split("/", 1)[1]
        include.append(leg)
    return {"include": include}


# --- command line ---------------------------------------------------------------


def _checked(args: argparse.Namespace) -> None:
    if not REPOSITORY.match(args.repository):
        raise SinkError(f"invalid repository {args.repository!r}")
    if not SHA.match(args.commit):
        raise SinkError("commit must be a 40-character lowercase SHA")
    if not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", args.ref):
        raise SinkError(f"invalid ref {args.ref!r}")


def _transport(repository: str) -> Transport:
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        raise SinkError(f"{TOKEN_ENV} is not set")
    return nightly_apply.http_transport(token, f"/repos/{repository}/")


def _cmd_publish(args: argparse.Namespace) -> int:
    _checked(args)
    files = {}
    for path in sorted(Path(args.sarif_dir).glob("*.sarif")):
        files[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    if not files:
        raise SinkError(f"no SARIF files in {args.sarif_dir}")
    transport = _transport(args.repository)
    retire = retired(open_alerts(transport, args.repository), files)
    results = publish(
        transport, args.repository, args.commit, args.ref, {**files, **retire}
    )
    print(
        json.dumps(
            {
                "repository": args.repository,
                "uploads": results,
                "retired": sorted(retire),
            }
        )
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    _checked(args)
    today = datetime.now(timezone.utc).date()
    if not args.ledger:
        raise SinkError("no finding-waiver ledger: the scan's governance gave none")
    ledger = load_ledger(Path(args.ledger), args.repository, today)
    bundles = {}
    for path in sorted(Path(args.bundle_dir).glob("*.json")):
        bundles[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    if not bundles:
        raise SinkError(f"no finding bundles in {args.bundle_dir}")
    audit = dependency_audit(
        args.python_project,
        args.pip_audit_outcome,
        args.pip_audit_packages,
        Path(args.pip_audit_report),
        Path(args.lock_dir),
    )
    print(
        f"pip-audit: {audit['status']}, {audit['packages']} packages, "
        f"{len(audit['vulns'])} known vulnerabilities"
    )
    result = report(
        _transport(args.repository),
        args.repository,
        args.commit,
        args.ref,
        args.creator,
        ledger,
        bundles,
        today.isoformat(),
        args.run_url,
        audit,
    )
    print(json.dumps({"repository": args.repository, **result}))
    if result["ratchet"] == "fail":
        print(
            "::error::ratchet failed: a tool's open count rose, or the "
            "dependency audit is incomplete, and the central finding-waiver "
            f"ledger does not cover it (issue #{result['issue']})"
        )
        return 1
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    org_ci = (ROOT / ".github/workflows/org-ci.yml").read_text(encoding="utf-8")
    security = (ROOT / SECURITY).read_text(encoding="utf-8")
    rendered = render_workflow(org_ci, security)
    target = ROOT / WORKFLOW
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != rendered:
            print(
                f"::error::{WORKFLOW} is stale: run python3 tools/nightly_sink.py render"
            )
            return 1
        print(f"{WORKFLOW} is current")
        return 0
    target.write_text(rendered, encoding="utf-8")
    print(f"wrote {WORKFLOW}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    del args
    matrix = plan_matrix(
        ROOT, os.environ.get("L9_NIGHTLY_REPOSITORY", "").strip(), os.environ
    )
    for leg in matrix["include"]:
        print(f"{leg['repository']}@{leg['revision']}")
    line = "matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n"
    target = os.environ.get("GITHUB_OUTPUT", "")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(line)
    else:
        sys.stdout.write(line)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("render").add_argument("--check", action="store_true")
    sub.add_parser("plan")
    for name in ("publish", "report"):
        command = sub.add_parser(name)
        command.add_argument("--repository", required=True)
        command.add_argument("--commit", required=True)
        command.add_argument("--ref", default="refs/heads/main")
    sub.choices["publish"].add_argument("--sarif-dir", required=True)
    sub.choices["report"].add_argument("--creator", required=True)
    sub.choices["report"].add_argument("--ledger", required=True)
    sub.choices["report"].add_argument("--bundle-dir", required=True)
    for flag in (
        "--python-project",
        "--pip-audit-outcome",
        "--pip-audit-packages",
        "--pip-audit-report",
        "--lock-dir",
    ):
        sub.choices["report"].add_argument(flag, required=True)
    sub.choices["report"].add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    try:
        handler = {
            "publish": _cmd_publish,
            "report": _cmd_report,
            "render": _cmd_render,
            "plan": _cmd_plan,
        }[args.command]
        return handler(args)
    except SinkError as error:
        print(f"::error::nightly sink: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
