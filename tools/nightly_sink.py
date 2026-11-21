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

    Ratchet: the open count may not rise night over night unless a waiver in
    ``.l9/nightly-ratchet-waivers.json`` covers it (repository, the most open
    alerts allowed, a reason, an approver and an expiry date). An unwaived rise
    is recorded on the issue and fails the run. The first night sets the
    baseline.

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
SNAPSHOT = re.compile(r"<!-- l9-nightly-snapshot (\{.*?\}) -->", re.DOTALL)
SNAPSHOT_SCHEMA = "l9.nightly-findings-snapshot/v1"
WAIVER_SCHEMA = "l9.nightly-ratchet-waivers/v1"
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
            instance = alert.get("most_recent_instance") or {}
            category = str(instance.get("category") or "")
            if not category.startswith(CATEGORY_PREFIX):
                continue
            rule = alert.get("rule") or {}
            location = (instance.get("location") or {}).get("path", "")
            alerts[int(alert["number"])] = {
                "rule": str(rule.get("id") or rule.get("name") or "?"),
                "severity": str(
                    rule.get("security_severity_level") or rule.get("severity") or ""
                ),
                "path": str(location),
                "url": str(alert.get("html_url") or ""),
            }
        if len(response) < 100:
            return alerts
        page += 1


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


def load_waivers(path: Path, today: date) -> dict[str, dict[str, Any]]:
    """Unexpired waivers by repository. A malformed ledger fails closed."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != WAIVER_SCHEMA or not isinstance(data.get("waivers"), list):
        raise SinkError(f"{path} is not a {WAIVER_SCHEMA} ledger")
    waivers: dict[str, dict[str, Any]] = {}
    for entry in data["waivers"]:
        required = ("repository", "allow_open", "reason", "approved_by", "expires")
        if not isinstance(entry, dict) or any(
            not entry.get(k) and entry.get(k) != 0 for k in required
        ):
            raise SinkError(f"{path}: a waiver needs {', '.join(required)}")
        if not isinstance(entry["allow_open"], int) or entry["allow_open"] < 0:
            raise SinkError(f"{path}: allow_open must be a non-negative integer")
        if date.fromisoformat(str(entry["expires"])) >= today:
            waivers[str(entry["repository"])] = entry
    return waivers


def snapshot_of(body: str) -> dict[str, Any] | None:
    match = SNAPSHOT.search(body or "")
    if not match:
        return None
    data = json.loads(match.group(1))
    return data if data.get("schema") == SNAPSHOT_SCHEMA else None


def judge(
    previous: Mapping[str, Any] | None,
    current: Mapping[int, Mapping[str, str]],
    waiver: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """New, fixed and still-open alerts, and the ratchet verdict."""
    before = set(previous.get("alerts", [])) if previous else set()
    now = set(current)
    previous_open = len(before) if previous else None
    rose = previous_open is not None and len(now) > previous_open
    waived = bool(rose and waiver and len(now) <= int(waiver["allow_open"]))
    return {
        "new": sorted(now - before) if previous else [],
        "fixed": sorted(before - now),
        "open": sorted(now),
        "previous_open": previous_open,
        "rose": rose,
        "waived": waived,
        "ratchet": "fail" if rose and not waived else "pass",
    }


def render(
    repository: str,
    commit: str,
    owners: Sequence[str],
    alerts: Mapping[int, Mapping[str, str]],
    verdict: Mapping[str, Any],
    night: str,
    run_url: str,
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
        f"- Open: **{len(verdict['open'])}**"
        + ("" if previous is None else f" (previous night {previous})"),
        f"- New: {len(verdict['new'])} · Fixed: {len(verdict['fixed'])}",
        f"- Ratchet: **{verdict['ratchet']}**"
        + (" (waived)" if verdict["waived"] else "")
        + (" (first night sets the baseline)" if previous is None else ""),
        "",
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
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "night": night,
        "commit": commit,
        "alerts": verdict["open"],
    }
    lines.append(f"<!-- l9-nightly-snapshot {json.dumps(snapshot, sort_keys=True)} -->")
    return "\n".join(lines) + "\n"


def _alert_line(number: int, alert: Mapping[str, str]) -> str:
    severity = f" ({alert['severity']})" if alert.get("severity") else ""
    return f"- [#{number}]({alert['url']}) `{alert['rule']}`{severity} in `{alert['path']}`"


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
        for issue in response:
            if "pull_request" in issue or ISSUE_MARKER not in str(
                issue.get("body") or ""
            ):
                continue
            if found is None or int(issue["number"]) > int(found["number"]):
                found = issue
        if len(response) < 100:
            return found
        page += 1


def report(
    transport: Transport,
    repository: str,
    commit: str,
    ref: str,
    creator: str,
    waivers: Mapping[str, Mapping[str, Any]],
    night: str,
    run_url: str,
) -> dict[str, Any]:
    alerts = open_alerts(transport, repository)
    owners = codeowners(transport, repository, ref)
    issue = find_issue(transport, repository, creator)
    previous = snapshot_of(str(issue.get("body") or "")) if issue else None
    verdict = judge(previous, alerts, waivers.get(repository))
    body = render(repository, commit, owners, alerts, verdict, night, run_url)
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
    if verdict["rose"]:
        word = "waived" if verdict["waived"] else "failed"
        note = (
            f"Ratchet {word} on {night}: open alerts rose from "
            f"{verdict['previous_open']} to {len(verdict['open'])}"
            + (
                f" (waiver: {waivers[repository]['reason']}, "
                f"approved by {waivers[repository]['approved_by']})"
                if verdict["waived"]
                else "; no waiver in .l9/nightly-ratchet-waivers.json covers it"
            )
            + f". Run: {run_url or 'n/a'}"
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
WAIVERS = Path(".l9/nightly-ratchet-waivers.json")
CUT = "      - name: Enforce repository verification\n"
EVENT_LINE = "          INPUT_EVENT: ${{ inputs.event || '' }}\n"
MATRIX_ID_LINE = "          INPUT_MATRIX_ID: canary-nightly-${{ matrix.slug }}\n"
MATRIX_ID = "nightly-${{ matrix.slug }}"
LEFT_OVER = re.compile(r"\bneeds\.|\bsecrets\.(?!L9_GITHUB_READ_TOKEN\b)")
TIMEOUT = 60

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
# with the new, fixed and still-open alerts. An open count that rises with no
# waiver in .l9/nightly-ratchet-waivers.json fails the run. Writes use the
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
      - name: Project every instance bundle to SARIF (SDK)
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
          name = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
          primary = env.get("L9_PRIMARY_LANGUAGE", "")
          if not name.match(primary) or not (runtime / "results.sarif").is_file():
              sys.exit("::error::the analyze steps produced no primary SARIF")
          shutil.copyfile(runtime / "results.sarif", out / (primary + ".sarif"))
          print("instance " + primary + ": results.sarif")
          errors = []
          bundles = sorted((runtime / "required-instances").glob("*/finding-bundle.json"))
          for bundle in bundles:
              instance = bundle.parent.name
              if instance == primary or not name.match(instance):
                  errors.append("unexpected instance directory " + instance)
                  continue
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

      - name: Fetch the sink tool (Core, this run's revision)
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
        uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3.3.0
        with:
          app-id: ${{ vars.L9_NIGHTLY_APP_ID }}
          private-key: ${{ secrets.L9_NIGHTLY_APP_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: ${{ matrix.name }}
          permission-security-events: write
          permission-issues: write
          permission-contents: read

      - name: Upload SARIF to code scanning
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
        env:
          L9_NIGHTLY_SINK_TOKEN: ${{ steps.sink_token.outputs.token }}
          GITHUB_TOKEN: ""
          GH_TOKEN: ""
          REPOSITORY: ${{ matrix.repository }}
          REVISION: ${{ matrix.revision }}
          BRANCH: ${{ matrix.branch }}
          CREATOR: ${{ steps.sink_token.outputs.app-slug }}[bot]
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          set -euo pipefail
          core="${RUNNER_TEMP}/nightly-core"
          python3 -I "${core}/tools/nightly_sink.py" report \\
            --repository "${REPOSITORY}" \\
            --commit "${REVISION}" \\
            --ref "refs/heads/${BRANCH}" \\
            --creator "${CREATOR}" \\
            --waivers "${core}/.l9/nightly-ratchet-waivers.json" \\
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


def render_workflow(org_ci: str) -> str:
    steps = sink_steps(org_ci)
    digest = hashlib.sha256(steps.encode("utf-8")).hexdigest()
    header = HEADER.replace("@@DIGEST@@", digest).replace("@@TIMEOUT@@", str(TIMEOUT))
    return header + steps + "\n" + SINK.replace("@@MATRIX_ID@@", MATRIX_ID)


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
    results = publish(
        _transport(args.repository), args.repository, args.commit, args.ref, files
    )
    print(json.dumps({"repository": args.repository, "uploads": results}))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    _checked(args)
    today = datetime.now(timezone.utc).date()
    waivers = load_waivers(Path(args.waivers), today)
    result = report(
        _transport(args.repository),
        args.repository,
        args.commit,
        args.ref,
        args.creator,
        waivers,
        today.isoformat(),
        args.run_url,
    )
    print(json.dumps({"repository": args.repository, **result}))
    if result["ratchet"] == "fail":
        print(
            f"::error::ratchet: open alerts rose from {result['previous_open']} "
            f"to {len(result['open'])} with no waiver (issue #{result['issue']})"
        )
        return 1
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    org_ci = (ROOT / ".github/workflows/org-ci.yml").read_text(encoding="utf-8")
    rendered = render_workflow(org_ci)
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
    sub.choices["report"].add_argument("--waivers", default=str(ROOT / WAIVERS))
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
