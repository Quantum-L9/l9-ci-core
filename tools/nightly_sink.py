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
from collections.abc import Set, Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nightly_apply  # noqa: E402

TOKEN_ENV = "L9_NIGHTLY_SINK_TOKEN"
CATEGORY_PREFIX = "l9-central/"
# org-ci.yml's CodeQL check publishes one category per language: codeql-<language>.
CODEQL_PREFIX = "codeql-"
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
    """Upload each named SARIF and wait for code scanning to accept it.

    A refused CodeQL upload (``codeql-<language>``) does not stop the others:
    code scanning refuses CodeQL analyses while a repository's CodeQL default
    setup is enabled (docs/codeql-check.md), and that must not cost the
    repository its Semgrep, gitleaks or Biome findings. Every other category
    is uploaded first; the refusal then fails the publish, by name, so the
    night stays red until default setup is turned off. Any other refusal
    fails at once, as before.
    """
    results = []
    refused = []
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
            if name.startswith(CODEQL_PREFIX):
                refused.append(f"{name} (HTTP {status})")
                continue
            raise SinkError(f"code scanning refused the {name} upload (HTTP {status})")
        upload = str(response["id"])
        state = _await_processing(transport, repository, upload, sleep)
        results.append({"name": name, "upload": upload, "status": state})
    if refused:
        raise SinkError(
            "code scanning refused the CodeQL upload(s) "
            + ", ".join(refused)
            + " after every other category was published; turn off the"
            " repository's CodeQL default setup (docs/codeql-check.md)"
        )
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
    plain = [name for name in files if not name.startswith(CODEQL_PREFIX)]
    first = files[min(plain or files)]
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


#: Categories whose tool is advisory (docs/biome-check.md): their alerts are
#: listed in the issue but never counted, so a first night or a rise cannot
#: fail the ratchet that guards the blocking categories.
ADVISORY_CATEGORIES = frozenset({"biome", "bandit"})


def advisory_alerts(alerts: Mapping[int, Mapping[str, str]]) -> set[int]:
    """Alert numbers whose category belongs to an advisory tool."""
    names = {f"{CATEGORY_PREFIX}{name}/" for name in ADVISORY_CATEGORIES}
    return {n for n, alert in alerts.items() if alert.get("category") in names}


def judge(
    previous: Mapping[str, Any] | None,
    current: Mapping[int, Mapping[str, str]],
    waived: Mapping[int, str],
    advisory: Set[int] = frozenset(),
) -> dict[str, Any]:
    """New, fixed, still-open and waived alerts, and the ratchet verdict.

    Waived alerts are open in code scanning but are not counted: the ratchet
    compares tonight's counted alerts with last night's. Advisory alerts are
    neither counted nor waived: they are listed apart.
    """
    before = set(previous.get("alerts", [])) if previous else set()
    listed = set(current) - set(waived)
    now = listed - set(advisory)
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
        "advisory": sorted(listed & set(advisory)),
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
        f"- Advisory tools (reported, never counted): {len(verdict.get('advisory', []))}",
        f"- Ratchet: **{verdict['ratchet']}**"
        + (" (first night sets the baseline)" if previous is None else ""),
        "",
    ]
    dependencies: Mapping[str, Any] = verdict.get("dependencies") or {}
    audited = dependencies.get("status", "not_applicable") != "not_applicable"
    if audited:
        lines[7:7] = [_dependency_count(dependencies)]
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
    if verdict.get("advisory"):
        lines += ["### Advisory (not counted)", ""]
        lines += [_alert_line(n, alerts[n]) for n in verdict["advisory"]]
        lines.append("")
    if audited:
        lines += _dependency_section(dependencies, audit)
    lines.append(_snapshot_line(night, commit, verdict))
    return "\n".join(lines) + "\n"


def _dependency_count(dependencies: Mapping[str, Any]) -> str:
    previous = dependencies["previous_open"]
    return (
        f"- pip-audit, known vulnerabilities: **{len(dependencies['open'])}**"
        f" in {dependencies['packages']} audited packages"
        + ("" if previous is None else f" (previous night {previous})")
    )


def _snapshot_line(night: str, commit: str, verdict: Mapping[str, Any]) -> str:
    """The issue's own record of the last accepted night, per tool."""
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "night": night,
        "commit": commit,
        "alerts": verdict["accepted"],
    }
    dependencies = verdict.get("dependencies")
    if dependencies and dependencies["accepted"] is not None:
        snapshot["dependencies"] = dependencies["accepted"]
    return f"<!-- l9-nightly-snapshot {json.dumps(snapshot, sort_keys=True)} -->"


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
    verdict = judge(previous, alerts, waived, advisory_alerts(alerts))
    dependencies = judge_dependencies(
        previous, audit, waived_dependencies(audit, ledger)
    )
    verdict["dependencies"] = dependencies
    if dependencies["ratchet"] == "fail":
        verdict["ratchet"] = "fail"
    body = render(repository, commit, owners, alerts, verdict, night, run_url, audit)
    number = _upsert_issue(transport, repository, issue, body)
    notes = _ratchet_notes(verdict, dependencies)
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


def _upsert_issue(
    transport: Transport,
    repository: str,
    issue: Mapping[str, Any] | None,
    body: str,
) -> int:
    """Open the rolling issue once; afterwards edit it in place."""
    if issue is None:
        status, response = transport(
            "POST", f"/repos/{repository}/issues", {"title": ISSUE_TITLE, "body": body}
        )
        if status != 201 or not isinstance(response, dict):
            raise SinkError(f"cannot open the rolling issue (HTTP {status})")
        return int(response["number"])
    number = int(issue["number"])
    status, _ = transport(
        "PATCH",
        f"/repos/{repository}/issues/{number}",
        {"title": ISSUE_TITLE, "body": body, "state": "open"},
    )
    if status != 200:
        raise SinkError(f"cannot update issue #{number} (HTTP {status})")
    return number


def _ratchet_notes(
    verdict: Mapping[str, Any], dependencies: Mapping[str, Any]
) -> list[str]:
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
    return notes


# --- the workflow ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = Path(".github/workflows/nightly-sink.yml")
SCAN = Path(".github/workflows/central-semgrep.yml")
# The scan's own enforcement is not run: the sink judges the findings (the
# ratchet), and a blocking gate would stop the job before the publication.
CUT = "      - name: Enforce central mode on SDK technical gate\n"
EVENT_LINE = "          INPUT_EVENT: ${{ inputs.event || '' }}\n"
MATRIX_ID_LINE = "          INPUT_MATRIX_ID: canary-nightly-${{ matrix.slug }}\n"
MATRIX_ID = "nightly-${{ matrix.slug }}"
LEFT_OVER = re.compile(r"\bneeds\.|\bsecrets\.(?!L9_GITHUB_READ_TOKEN\b)")
TIMEOUT = 90
AUDIT_STEPS = (
    "Detect project languages",
    "Set up Python",
    "Python dependency audit (pip-audit)",
)
AUDIT_REPORT = "${{ runner.temp }}/pip-audit.json"
AUDIT_ID = "        id: pip_audit\n"
AUDIT_LEFT_OVER = re.compile(r"\b(inputs|needs)\.|github\.(event|sha|repository)\b")

HEADER = """\
# GENERATED by tools/nightly_sink.py render from central-semgrep.yml. Do not
# edit: change central-semgrep.yml (or the tool) and run `python3
# tools/nightly_sink.py render`. tests/tools/test_nightly_sink.py fails while
# this file and central-semgrep.yml disagree.
#
# The nightly findings sink (X4), and since the operator decision of 2026-11-21
# the only place Semgrep runs (docs/semgrep-runs-nightly.md). Each
# night every repository declared in .l9/promotion-canary.json (nightly not
# false) is checked out at its main head and scanned by central-semgrep.yml's
# steps, verbatim apart from the target identity, as a `nightly` event: a clean
# checkout, the whole tree, no baseline, every planned instance (languages and
# the consumer rulepack). The scan's own enforcement step is not run; the sink
# judges the findings. Every instance bundle is projected to
# SARIF by the SDK and uploaded to that repository's code scanning (one
# category per instance), and one rolling "Nightly findings" issue is updated
# with the new, fixed and still-open alerts. org-ci.yml's `pip-audit` check
# (inlined, not copied) audits each repository's full resolved lock, and its
# known vulnerabilities go to the same issue. org-ci.yml's `Biome` check runs
# on the whole tree, and bandit runs only here (nightly only, operator
# 2026-11-21), both advisory: their alerts are listed, never counted. An open
# Semgrep or dependency count that rises, or an incomplete audit, fails
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
      # --- nightly sink (not part of central-semgrep.yml) ---
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

@@BIOME@@
      - name: Project Biome's SARIF for code scanning
        if: ${{ !cancelled() && steps.biome_check.outcome == 'success' }}
        run: |
          set -euo pipefail
          python3 -I - <<'PY'
          import json
          import os
          from pathlib import Path

          # Biome writes absolute file URIs; code scanning needs them relative
          # to the repository. Its alerts are one more category, beside each
          # Semgrep instance's: l9-central/biome/.
          source = Path(os.environ["RUNNER_TEMP"], "biome.sarif")
          if not source.is_file():
              print("Biome wrote no SARIF (no Biome configuration): no biome category tonight")
              raise SystemExit(0)
          root = Path(os.environ["GITHUB_WORKSPACE"]).resolve().as_uri() + "/"
          sarif = json.loads(source.read_text(encoding="utf-8"))
          for run in sarif.get("runs", []):
              for result in run.get("results", []):
                  for location in result.get("locations", []):
                      artifact = location.get("physicalLocation", {}).get("artifactLocation", {})
                      uri = str(artifact.get("uri", ""))
                      if uri.startswith(root):
                          artifact["uri"] = uri[len(root):]
                      elif uri.startswith("/"):
                          artifact["uri"] = os.path.relpath(uri, os.environ["GITHUB_WORKSPACE"])
          out = Path(os.environ["RUNNER_TEMP"], "nightly-sarif", "biome.sarif")
          out.parent.mkdir(parents=True, exist_ok=True)
          out.write_text(json.dumps(sarif), encoding="utf-8")
          print("instance biome: projected")
          PY

@@GITLEAKS@@
@@HYGIENE@@
@@CODEQL@@
@@ACTIONLINT@@
      - name: Project CodeQL's SARIF for code scanning
        if: ${{ !cancelled() && steps.codeql_analyze.outcome == 'success' }}
        env:
          LANGUAGES: ${{ steps.codeql_scope.outputs.languages }}
          RESULTS: ${{ steps.codeql_analyze.outputs.directory }}
        run: |
          set -euo pipefail
          # One category per language, beside each Semgrep instance's:
          # l9-central/codeql-<language>/. The whole tree, so every result.
          mkdir -p "${RUNNER_TEMP}/nightly-sarif"
          for language in ${LANGUAGES}; do
            cp "${RESULTS}/head-${language}.sarif" "${RUNNER_TEMP}/nightly-sarif/codeql-${language}.sarif"
            echo "instance codeql-${language}: projected"
          done

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

      # bandit runs nightly only, advisory (operator, 2026-11-21; it is not a
      # pull-request check). The whole default branch, every severity and
      # confidence; tests are excluded at any depth (a measured 3,581 of
      # 4,694 Cursor-Governance findings sat in nested test paths). Findings
      # go to code scanning (l9-central/bandit/, listed, never counted) and
      # the complete JSON report is kept as an artifact, so bandit can be
      # compared with SonarCloud before it is promoted or retired. Exit 0 (no
      # finding) and 1 (findings) are both a completed scan; anything else is
      # a scan that did not complete and fails the step.
      - id: bandit
        name: Python static security scan (bandit, nightly, advisory)
        if: ${{ !cancelled() && steps.detect.outputs.python == 'true' }}
        run: |
          set -euo pipefail
          python -m pip install --only-binary :all: "bandit[sarif]==1.8.6"
          exclude="./venv,./.venv,./tests,./build,./dist,*/tests/*,*/test_*.py"
          sarif_dir="${RUNNER_TEMP}/nightly-sarif"
          mkdir -p "${sarif_dir}"
          for format in json sarif; do
            target="${RUNNER_TEMP}/bandit.json"
            [ "${format}" = "sarif" ] && target="${sarif_dir}/bandit.sarif"
            status=0
            bandit -q -r . -f "${format}" -o "${target}" --exclude "${exclude}" || status=$?
            if [ "${status}" -gt 1 ]; then
              echo "::error title=bandit::bandit -f ${format} exited ${status}: the scan did not complete"
              exit "${status}"
            fi
          done
          python3 -I - "${RUNNER_TEMP}/bandit.json" <<'PY'
          import json
          import os
          import sys
          from collections import Counter

          data = json.load(open(sys.argv[1], encoding="utf-8"))
          results = data.get("results", [])
          by = Counter((r.get("issue_severity", "?"), r.get("issue_confidence", "?")) for r in results)
          line = (
              f"- bandit (nightly, advisory): {len(results)} finding(s); "
              + ", ".join(f"{s}/{c}: {n}" for (s, c), n in sorted(by.items()))
          )
          print(line)
          with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
              stream.write(line + "\\n")
          PY

      - name: Keep bandit's full JSON report
        if: ${{ !cancelled() && steps.bandit.outcome == 'success' }}
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: bandit-${{ matrix.slug }}
          path: ${{ runner.temp }}/bandit.json
          if-no-files-found: error
          retention-days: 30

      # Each sink step runs only when the one it depends on succeeded, so a
      # failed dependency audit still reaches the issue, while a failed or
      # partial upload never writes a stale state into its snapshot.
      - id: sink_tool
        name: Fetch the sink tool (Core, this run's revision)
        # A Biome, gitleaks, no-bytecode, sha-pins, CodeQL, actionlint or
        # bandit run that failed (not one with findings: here each reports
        # findings instead of failing) would drop its category and so close
        # its alerts: nothing publishes.
        if: ${{ !cancelled() && steps.project_sarif.outcome == 'success' && steps.biome_check.outcome != 'failure' && steps.gitleaks.outcome != 'failure' && steps.no_bytecode.outcome != 'failure' && steps.sha_pins.outcome != 'failure' && steps.codeql_analyze.outcome != 'failure' && steps.actionlint_check.outcome != 'failure' && steps.bandit.outcome != 'failure' }}
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
            --pip-audit-report "${RUNNER_TEMP}/pip-audit.json" \\
            --lock-dir "${GITHUB_WORKSPACE}" \\
            --run-url "${RUN_URL}"
"""


def _canary() -> Any:
    import promotion_canary  # noqa: PLC0415 — render and plan only

    return promotion_canary


def sink_steps(scan: str) -> str:
    """The scan's steps (central-semgrep.yml) as a nightly scan of the matrix
    repository, up to its own enforcement."""
    pc = _canary()
    try:
        steps = pc.retarget(pc.analyze_steps(scan), "nightly")
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    for old, new in (
        (EVENT_LINE, "          INPUT_EVENT: nightly\n"),
        (MATRIX_ID_LINE, f"          INPUT_MATRIX_ID: {MATRIX_ID}\n"),
    ):
        if steps.count(old) != 1:
            raise SinkError(f"{SCAN}'s steps no longer contain {old.strip()!r} once")
        steps = steps.replace(old, new)
    lines = steps.splitlines(keepends=True)
    try:
        cut = lines.index(CUT)
    except ValueError as error:
        raise SinkError(f"{SCAN}'s steps have no {CUT.strip()!r} step") from error
    while cut and (
        not lines[cut - 1].strip() or lines[cut - 1].lstrip().startswith("#")
    ):
        cut -= 1
    kept = "".join(lines[:cut]).rstrip("\n") + "\n"
    # The sink declares only `repository`: every other input is its default.
    kept = pc.undeclared_defaults(kept, frozenset({"repository"}))
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


def pip_audit_workflow(org_ci: str) -> str:
    """org-ci.yml's `pip-audit` check as a one-job workflow (its source)."""
    blocks = _canary().job_blocks(org_ci)
    if "pip-audit" not in blocks:
        raise SinkError("org-ci.yml has no `pip-audit` job to audit nightly")
    return "on:\n  workflow_call:\njobs:\n  pip-audit:\n" + blocks["pip-audit"]


def audit_steps(security: str) -> str:
    """The `pip-audit` check's dependency audit, as the nightly runs it.

    ``security`` is the one-job workflow of org-ci.yml's `pip-audit` check
    (``pip_audit_workflow``). Its detection, Python and pip-audit steps are
    taken verbatim, never copied by hand, so the nightly audits exactly what
    the pull-request gate audits. Only the event identity is resolved: the nightly
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
        steps, _, _ = pc._single_job(security, "org-ci.yml job 'pip-audit'")
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
            raise SinkError(f"the pip-audit check no longer has one {name!r} step")
        chosen.append(found[0].rstrip("\n") + "\n")
    text = "".join(chosen)
    defaults = {**pc._inputs(security), "pip-audit-report": AUDIT_REPORT}

    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in defaults:
            raise SinkError(
                f"the pip-audit check reads input {key!r}, which has no value"
            )
        return json.dumps(defaults[key])

    text = re.sub(r"\$\{\{\s*inputs\.([a-z0-9-]+)\s*\}\}", value, text)
    text = text.replace("${{ github.event_name }}", "nightly")
    text = re.sub(r"\$\{\{\s*github\.event\.[a-z_.]+\s*\}\}", '""', text)
    text = text.replace("${{ github.sha }}", "${{ matrix.revision }}")
    if text.count(AUDIT_ID) != 1:
        raise SinkError("the pip-audit check's audit step no longer has id pip_audit")
    left = [
        line.strip()
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and AUDIT_LEFT_OVER.search(line)
    ]
    if left:
        raise SinkError(
            "the pip-audit check references what the sink cannot satisfy: "
            + "; ".join(left)
        )
    return (
        "      # --- org-ci.yml's pip-audit check, full lock (generated, not copied) ---\n"
        + text
    )


BIOME_JOB = "biome"
BIOME_MODE_LINE = "          L9_BIOME_MODE: blocking\n"
ORG_CI = Path(".github/workflows/org-ci.yml")


def biome_steps(org_ci: str) -> str:
    """org-ci.yml's `Biome` job, run on the whole tree and advisory.

    The job's own steps after its checkout (the sink's scan already checked
    the repository out at its main head), retargeted like any canary sibling:
    a schedule is not a pull request, so Biome judges the whole tree. Advisory,
    so a finding reaches code scanning and the issue instead of failing the
    night; an infrastructure failure still fails the step.
    """
    pc = _canary()
    blocks = pc.job_blocks(org_ci)
    if BIOME_JOB not in blocks:
        raise SinkError("org-ci.yml has no `biome` job to run nightly")
    workflow = "on:\n  workflow_call:\njobs:\n  biome:\n" + blocks[BIOME_JOB]
    try:
        steps, _, _ = pc._single_job(workflow, "org-ci.yml job 'biome'")
        steps = pc._substitute(steps, {}, "org-ci.yml job 'biome'")
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    items = [i for i in re.split(r"(?m)^(?= {6}- )", steps) if i.strip()]
    if not items or "name: Checkout immutable event revision" not in items[0]:
        raise SinkError("org-ci.yml's biome job no longer starts with its checkout")
    text = "".join(items[1:])
    if text.count(BIOME_MODE_LINE) != 1:
        raise SinkError("org-ci.yml's biome job no longer sets L9_BIOME_MODE once")
    text = text.replace(BIOME_MODE_LINE, "          L9_BIOME_MODE: advisory\n")
    name = "      - name: Run Biome check (blocking)\n"
    if text.count(name) != 1:
        raise SinkError("org-ci.yml's biome job no longer names its run step once")
    text = text.replace(
        name, "      - name: Run Biome check (advisory in the nightly sink)\n"
    )
    return (
        "      # --- org-ci.yml's Biome check, whole tree, advisory (generated) ---\n"
        + text.rstrip("\n")
        + "\n"
    )


GITLEAKS_JOB = "gitleaks"
GITLEAKS_STEP = "Secret scan (gitleaks)"
GITLEAKS_SARIF = "${{ runner.temp }}/nightly-sarif/gitleaks.sarif"
# The step's PR-mode settings, each replaced once by its nightly value.
GITLEAKS_NIGHTLY = (
    ("          GITLEAKS_SCOPE: event\n", "          GITLEAKS_SCOPE: history\n"),
    ("          GITLEAKS_TARGET: git\n", "          GITLEAKS_TARGET: dir\n"),
    (
        '          GITLEAKS_FINDINGS_FAIL: "true"\n',
        '          GITLEAKS_FINDINGS_FAIL: "false"\n',
    ),
    ('          GITLEAKS_SARIF: ""\n', f"          GITLEAKS_SARIF: {GITLEAKS_SARIF}\n"),
)


def gitleaks_steps(org_ci: str) -> str:
    """org-ci.yml's `gitleaks` check, run on the whole tree and reported.

    The check's own scan step, verbatim apart from its mode: the whole tree of
    the default branch (``gitleaks dir``; the sink's checkout is shallow, and a
    secret that is still in the code is the one to fix), findings written as
    SARIF for code scanning (``l9-central/gitleaks/``, counted by the ratchet)
    instead of failing the step. A scan that does not complete still fails it.
    """
    pc = _canary()
    blocks = pc.job_blocks(org_ci)
    if GITLEAKS_JOB not in blocks:
        raise SinkError("org-ci.yml has no `gitleaks` job to run nightly")
    workflow = "on:\n  workflow_call:\njobs:\n  gitleaks:\n" + blocks[GITLEAKS_JOB]
    try:
        steps, _, _ = pc._single_job(workflow, "org-ci.yml job 'gitleaks'")
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    items = re.split(r"(?m)^(?= {6}- )", steps)
    found = [i for i in items if f"name: {GITLEAKS_STEP}\n" in i]
    if len(found) != 1:
        raise SinkError(f"the gitleaks check no longer has one {GITLEAKS_STEP!r} step")
    text = found[0].rstrip("\n") + "\n"
    for old, new in GITLEAKS_NIGHTLY:
        if text.count(old) != 1:
            raise SinkError(f"the gitleaks step no longer sets {old.strip()!r} once")
        text = text.replace(old, new)
    text = text.replace("${{ github.event_name }}", "nightly")
    text = re.sub(r"\$\{\{\s*github\.event\.[a-z_.]+\s*\}\}", '""', text)
    text = text.replace("${{ github.sha }}", "${{ matrix.revision }}")
    head = f"      - name: {GITLEAKS_STEP}\n"
    if not text.startswith(head) or "        id: gitleaks\n" not in text:
        raise SinkError("the gitleaks step no longer starts with its name and id")
    text = text.replace(head, head + "        if: ${{ !cancelled() }}\n", 1)
    return (
        "      # --- org-ci.yml's gitleaks check, whole tree, reported (generated) ---\n"
        + text
    )


#: org-ci.yml's no-bytecode and sha-pins checks: (job, step id).
HYGIENE_CHECKS = (("no-bytecode", "no_bytecode"), ("sha-pins", "sha_pins"))


def hygiene_steps(org_ci: str) -> str:
    """org-ci.yml's no-bytecode and sha-pins checks, on every file, reported.

    Each check's own step, verbatim apart from the event (a night has no base,
    so every tracked file is judged) and its mode: findings go to code
    scanning as ``l9-central/<check>/`` (counted) instead of failing the step.
    """
    pc = _canary()
    blocks = pc.job_blocks(org_ci)
    out = []
    for job, identifier in HYGIENE_CHECKS:
        if job not in blocks:
            raise SinkError(f"org-ci.yml has no `{job}` job to run nightly")
        workflow = f"on:\n  workflow_call:\njobs:\n  {job}:\n" + blocks[job]
        try:
            steps, _, _ = pc._single_job(workflow, f"org-ci.yml job {job!r}")
        except pc.CanaryError as error:
            raise SinkError(str(error)) from error
        items = re.split(r"(?m)^(?= {6}- )", steps)
        found = [i for i in items if f"        id: {identifier}\n" in i]
        if len(found) != 1:
            raise SinkError(f"the {job} check no longer has one {identifier!r} step")
        text = found[0].rstrip("\n") + "\n"
        for old, new in (
            ('          FINDINGS_FAIL: "true"\n', '          FINDINGS_FAIL: "false"\n'),
            (
                '          SARIF: ""\n',
                f"          SARIF: ${{{{ runner.temp }}}}/nightly-sarif/{job}.sarif\n",
            ),
        ):
            if text.count(old) != 1:
                raise SinkError(f"the {job} step no longer sets {old.strip()!r} once")
            text = text.replace(old, new)
        text = text.replace("${{ github.event_name }}", "nightly")
        text = re.sub(r"\$\{\{\s*github\.event\.[a-z_.]+\s*\}\}", '""', text)
        text = text.replace("${{ github.sha }}", "${{ matrix.revision }}")
        head = re.match(r"      - name: [^\n]+\n", text)
        if head is None or "${{ github." in text or "${{ inputs." in text:
            raise SinkError(f"the {job} step cannot be run nightly as written")
        text = (
            text[: head.end()]
            + "        if: ${{ !cancelled() }}\n"
            + text[head.end() :]
        )
        out.append(
            f"      # --- org-ci.yml's {job} check, every file (generated) ---\n" + text
        )
    return "".join(out).rstrip("\n")


CODEQL_JOB = "codeql"
CODEQL_STEPS = ("codeql_scope", "codeql_install", "codeql_analyze")


def codeql_steps(org_ci: str) -> str:
    """org-ci.yml's CodeQL check, run on the whole default branch.

    The check's own language, install and analyze steps, verbatim apart from
    the event: a night is not a pull request, merge group or push, so there is
    no base and the analyze step runs once, on the whole tree. Its verdict
    step is not run (with no base nothing is new); the SARIF goes to code
    scanning instead, one ``l9-central/codeql-<language>/`` category each.
    """
    pc = _canary()
    blocks = pc.job_blocks(org_ci)
    if CODEQL_JOB not in blocks:
        raise SinkError("org-ci.yml has no `codeql` job to run nightly")
    workflow = "on:\n  workflow_call:\njobs:\n  codeql:\n" + blocks[CODEQL_JOB]
    try:
        steps, _, _ = pc._single_job(workflow, "org-ci.yml job 'codeql'")
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    items = re.split(r"(?m)^(?= {6}- )", steps)
    chosen = []
    for identifier in CODEQL_STEPS:
        found = [i for i in items if f"        id: {identifier}\n" in i]
        if len(found) != 1:
            raise SinkError(f"the CodeQL check no longer has one {identifier!r} step")
        text = found[0].rstrip("\n") + "\n"
        name = re.match(r"      - name: [^\n]+\n", text)
        if name is None:
            raise SinkError(
                f"the CodeQL {identifier!r} step no longer starts with its name"
            )
        chosen.append(
            text[: name.end()]
            + "        if: ${{ !cancelled() }}\n"
            + text[name.end() :]
        )
    text = "".join(chosen)
    text = text.replace("${{ github.event_name }}", "nightly")
    text = re.sub(r"\$\{\{\s*github\.event\.[a-z_.]+\s*\}\}", '""', text)
    if "${{ github." in text or "${{ inputs." in text:
        raise SinkError("the CodeQL steps read an event value the sink cannot supply")
    return "      # --- org-ci.yml's CodeQL check, whole tree (generated) ---\n" + text


ACTIONLINT_JOB = "actionlint"
ACTIONLINT_STEPS = ("actionlint_scope", "actionlint_install", "actionlint_check")
ACTIONLINT_SARIF = "${{ runner.temp }}/nightly-sarif/actionlint.sarif"
# The check's PR-mode settings, each replaced once by its nightly value.
ACTIONLINT_NIGHTLY = (
    (
        '          ACTIONLINT_FINDINGS_FAIL: "true"\n',
        '          ACTIONLINT_FINDINGS_FAIL: "false"\n',
    ),
    (
        '          ACTIONLINT_SARIF: ""\n',
        f"          ACTIONLINT_SARIF: {ACTIONLINT_SARIF}\n",
    ),
)


def actionlint_steps(org_ci: str) -> str:
    """org-ci.yml's actionlint check, on every workflow, reported.

    The check's own scope, install and run steps, verbatim apart from the
    event (a night has no base, so every workflow is judged) and its mode:
    findings are written as SARIF (``l9-central/actionlint/``, counted)
    instead of failing the step. A run that does not complete still fails it.
    """
    pc = _canary()
    blocks = pc.job_blocks(org_ci)
    if ACTIONLINT_JOB not in blocks:
        raise SinkError("org-ci.yml has no `actionlint` job to run nightly")
    workflow = "on:\n  workflow_call:\njobs:\n  actionlint:\n" + blocks[ACTIONLINT_JOB]
    try:
        steps, _, _ = pc._single_job(workflow, "org-ci.yml job 'actionlint'")
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    items = re.split(r"(?m)^(?= {6}- )", steps)
    chosen = []
    for identifier in ACTIONLINT_STEPS:
        found = [i for i in items if f"        id: {identifier}\n" in i]
        if len(found) != 1:
            raise SinkError(
                f"the actionlint check no longer has one {identifier!r} step"
            )
        chosen.append(found[0].rstrip("\n") + "\n")
    text = "".join(chosen)
    # Run whatever an earlier, unrelated step concluded (as the other
    # inlined checks do): a skipped check would drop its category tonight.
    gate = "        if: steps.actionlint_scope.outputs.applies == 'true'\n"
    if text.count(gate) != 2:
        raise SinkError("the actionlint steps no longer gate on their scope twice")
    text = text.replace(
        gate,
        "        if: ${{ !cancelled() && steps.actionlint_scope.outputs.applies == 'true' }}\n",
    )
    scope = "        id: actionlint_scope\n"
    text = text.replace(scope, "        if: ${{ !cancelled() }}\n" + scope, 1)
    for old, new in ACTIONLINT_NIGHTLY:
        if text.count(old) != 1:
            raise SinkError(f"the actionlint step no longer sets {old.strip()!r} once")
        text = text.replace(old, new)
    text = text.replace("${{ github.event_name }}", "nightly")
    text = re.sub(r"\$\{\{\s*github\.event\.[a-z_.]+\s*\}\}", '""', text)
    text = text.replace("${{ github.sha }}", "${{ matrix.revision }}")
    if "${{ github." in text or "${{ inputs." in text:
        raise SinkError(
            "the actionlint steps read an event value the sink cannot supply"
        )
    return (
        "      # --- org-ci.yml's actionlint check, every workflow (generated) ---\n"
        + text
    )


def render_workflow(scan: str, org_ci: str) -> str:
    steps = sink_steps(scan)
    biome = biome_steps(org_ci)
    gitleaks = gitleaks_steps(org_ci)
    hygiene = hygiene_steps(org_ci)
    codeql = codeql_steps(org_ci)
    actionlint = actionlint_steps(org_ci)
    audit = audit_steps(pip_audit_workflow(org_ci))
    digest = hashlib.sha256(
        (steps + biome + gitleaks + hygiene + codeql + actionlint + audit).encode(
            "utf-8"
        )
    ).hexdigest()
    header = HEADER.replace("@@DIGEST@@", digest).replace("@@TIMEOUT@@", str(TIMEOUT))
    sink = (
        SINK.replace("@@MATRIX_ID@@", MATRIX_ID)
        .replace("@@BIOME@@\n", biome + "\n")
        .replace("@@GITLEAKS@@\n", gitleaks + "\n")
        .replace("@@HYGIENE@@\n", hygiene + "\n")
        .replace("@@CODEQL@@\n", codeql + "\n")
        .replace("@@ACTIONLINT@@\n", actionlint + "\n")
        .replace("@@AUDIT@@\n", audit + "\n")
    )
    return header + steps + "\n" + sink


def plan_matrix(root: Path, only: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Each declared repository's main head; ``only`` narrows it to one."""
    pc = _canary()
    try:
        entries = pc.load_declaration(root)
    except pc.CanaryError as error:
        raise SinkError(str(error)) from error
    # A canary-only entry ("nightly": false) is judged on Core pull requests
    # but not scanned nightly: the sink writes code scanning and an issue there.
    if only:
        entries = [e for e in entries if e["repository"].lower() == only.lower()]
        if not entries:
            raise SinkError(f"{only} is not declared in {pc.DECLARATION}")
        if not entries[0]["nightly"]:
            raise SinkError(
                f"{only} is canary-only (nightly: false) in {pc.DECLARATION}"
            )
    entries = [e for e in entries if e["nightly"]]
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
    scan = (ROOT / SCAN).read_text(encoding="utf-8")
    org_ci = (ROOT / ORG_CI).read_text(encoding="utf-8")
    rendered = render_workflow(scan, org_ci)
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
