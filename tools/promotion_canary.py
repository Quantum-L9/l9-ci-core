#!/usr/bin/env python3
"""Core's promotion canary: judge a Core pull request by what it changes in
the organization's required check (``.l9/org-runtime-contract.yaml``
``entrypoint.required_check``) for declared repositories, before it merges.

A Core pull request that changes what the required job runs (``org-ci.yml``,
the SDK pin or compatibility manifest, an action ``org-ci.yml`` calls) runs the
job against the current main head of every repository declared in
``.l9/promotion-canary.json`` and is judged differentially (AGENTS.md §40): it
fails only when it makes a repository worse than the base pins do. A
repository already red for its own reasons does not block the Core change, so
the pin advance that fixes it is never deadlocked.

``org-ci.yml`` targets ``github.repository`` at ``github.sha`` and no caller can
redirect it, so the canary never calls it. ``render`` writes
``.github/workflows/promotion-canary.yml`` with two legs, each the ``analyze``
job's steps verbatim apart from the target identity:

* the **head** leg, rendered from this tree's ``org-ci.yml``: a pull request
  runs its own workflow files, so this is always the pull request's steps;
* the **base** leg, rendered from ``origin/main``'s ``org-ci.yml`` when
  ``render`` ran.

Each leg runs everything ``org-ci.yml`` runs, not only ``analyze``: a job that
calls a Core reusable workflow (``uses: Quantum-L9/l9-ci-core/.github/workflows/
<file>@<sha>``, today ``security``) is inlined into the leg ahead of the
``analyze`` steps from that workflow at that pin. Its target identity is
retargeted the same way, and its event-scoped values describe a push of the
evaluated head (one commit), since a main head has no pull request range. Each
inlined step continues on error and their outcomes become the job result that
``analyze`` reads as ``needs.<job>.result``. The inlined bytes are embedded and,
like the base leg, re-rendered and compared by the plan, which has the history
the pins need; a stale render fails the verdict with the instruction to
re-render.

A third leg, **admitted**, closes the gap the first of the two promotion
steps leaves (AGENTS.md §43): a pull request that changes the SDK revision
``.l9/sdk-compatibility.yaml`` admits by default without moving ``org-ci.yml``
would otherwise run the current SDK on both legs and pass without ever running
the revision it admits. When the tree's default differs from ``org-ci.yml``'s
``sdk-revision`` fallback, the plan names it ``admitted`` and uploads the pull
request's ``provision-sdk`` action with that manifest; the admitted leg is the
head leg with its ``provision-sdk`` step run from that upload (the pinned
action refuses a revision its own manifest does not admit) at the admitted
revision, and is judged against the pull request's pins.

Both run inline in the pull request's own run: no write permission, no
``pull_request_target``, no floating reference and no composite action
(AGENTS.md §8, ``tests/workflows/test_workflow_permissions.py``).

The plan compares the ``analyze`` steps at the base tip (``HEAD^1`` of the
merge commit) and at the pull request. Equal: the base pins would run the same
steps against the same heads, so the base result *is* the pull request's and
the base leg does not run. Different: the plan re-renders the base tip's steps
and requires them to be byte-identical to the embedded base leg; only then does
the base leg run. A stale embedded base (main's ``org-ci.yml`` moved since the
pull request rendered) fails the verdict with the instruction to re-render. A
pull request that leaves the steps alone never runs the base leg, so a stale
embedded base never blocks it.

Subcommands (stdlib only; run with ``python3 -I``):

``render [--check]``
    Write the workflow, taking the base leg from ``origin/main``; or verify the
    committed workflow against this tree's ``org-ci.yml`` and its own embedded
    base leg.

``plan [--force]``
    Decide whether the checked-out change touches a canary path, whether it
    changes the ``analyze`` steps, and whether the embedded base leg is the
    base tip's; resolve each declared repository's main head.

``verdict``
    Judge every repository before and after, and print the table.

Exit codes: 0 pass, 1 fail, 2 invalid input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ORG_CI = Path(".github/workflows/org-ci.yml")
# The Semgrep chain, off the pull-request path since the operator decision of
# 2026-11-21 (docs/semgrep-runs-nightly.md). The nightly sink runs it;
# the canary's nightly-scan legs prove a change to it before promotion.
SCAN = Path(".github/workflows/central-semgrep.yml")
CANARY = Path(".github/workflows/promotion-canary.yml")
DECLARATION = Path(".l9/promotion-canary.json")
SCHEMA = "l9.promotion-canary/v1"
REPOSITORY = re.compile(r"^Quantum-L9/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
CORE_ACTION = re.compile(
    r"uses:\s*Quantum-L9/l9-ci-core/\.github/actions/([A-Za-z0-9._-]+)@"
)
NO_VERDICT = ("incomplete", "cancelled", "error")
BASE_BEGIN = (
    "  # --- base pins leg: begin (rendered from origin/main's org-ci.yml) ---\n"
)
# The leg families: org-ci.yml's required check, and the nightly Semgrep scan
# (central-semgrep.yml). Each has a pull-request side and a base-pins side;
# the admitted-SDK leg runs the scan, the only family that provisions the SDK.
SCAN_SIDE = "scan"
SCAN_BASE_SIDE = "scanbase"
# The scan legs run as the nightly sink runs them: a `nightly` event, so the
# nightly profile (whole tree, no baseline, advisory findings). Each side of
# the comparison is then the same kind of run, and a finding is judged by
# whether the pull request adds it, never by the mode's verdict.
EVENT_LINE = "          INPUT_EVENT: ${{ inputs.event || '' }}\n"
NIGHTLY_EVENT_LINE = "          INPUT_EVENT: nightly\n"
BASE_END = "  # --- base pins leg: end ---\n"
REUSABLE = re.compile(
    r"^Quantum-L9/l9-ci-core/(\.github/workflows/[A-Za-z0-9._-]+\.ya?ml)@([0-9a-f]{40})$"
)
JOB_NAME = re.compile(r"^ {2}([a-z][a-z0-9_-]*):\n$")
INLINE_BEGIN = "      # --- inlined org-ci job {key}: begin ---\n"
INLINE_END = "      # --- inlined org-ci job {key}: end ---\n"
LEG_TIMEOUT = 30
SIBLING_TIMEOUT = 30
PROVISION_ACTION = Path(".github/actions/provision-sdk/action.yml")
SDK_COMPATIBILITY = Path(".l9/sdk-compatibility.yaml")
ADMISSION_ARTIFACT = "canary-admitted-core"
ADMITTED_CORE = "${{ runner.temp }}/canary-admitted-core"
SDK_STEP = re.compile(r"^ {6}- id: sdk\n(?:(?! {6}- )[^\n]*\n)*", re.MULTILINE)
SDK_FALLBACK = re.compile(
    r"steps\.runtime\.outputs\['sdk-revision'\] \|\| '([0-9a-f]{40})'"
)
DEFAULT_REVISION = re.compile(
    r"^default:\n(?: {2}(?!revision:)[^\n]*\n)* {2}revision: ([0-9a-f]{40})$",
    re.MULTILINE,
)

# Paths whose change alters what the required check (org-ci.yml) runs, or the
# canary itself. Core actions are added from org-ci.yml's own `uses:` lines.
STATIC_PATHS = (
    str(ORG_CI),
    str(SCAN),
    ".l9/sdk-compatibility.yaml",
    str(DECLARATION),
    str(CANARY),
    "tools/promotion_canary.py",
)

# Target identity in the analyze job, and what a leg uses instead. Each must
# occur at least once; anything target-shaped that is left is refused. The
# matrix id names the leg's artifact set, so every leg has its own.
MATRIX_REVISION = "${{ matrix.revision }}"
REPLACEMENTS = (
    ("${{ github.repository }}", "${{ matrix.repository }}"),
    ("${{ github.sha }}", MATRIX_REVISION),
    ("${{ github.ref }}", "refs/heads/${{ matrix.branch }}"),
    (
        "INPUT_MATRIX_ID: ${{ inputs.matrix-id || 'org-semgrep' }}",
        "INPUT_MATRIX_ID: canary-@@SIDE@@-${{ matrix.slug }}",
    ),
    (
        "TOKEN: ${{ github.token }}",
        "TOKEN: ${{ matrix.private && secrets.L9_GITHUB_READ_TOKEN || github.token }}",
    ),
)
# Applied where org-ci.yml carries them. A base leg renders an older org-ci.yml
# that may predate them; the current org-ci.yml must carry every one
# (tests/workflows/test_promotion_canary.py), so a head leg never lacks them.
OPTIONAL_REPLACEMENTS = (
    # The artifact handoff names the workflow head; only a source that hands
    # off artifacts (the scan) carries it.
    (
        "${{ github.event.pull_request.head.sha || github.sha }}",
        MATRIX_REVISION,
    ),
    # Row 27: a leg checks out a consumer's main head inside a Core
    # pull_request run, so the consumer's make runs as a push of that head,
    # never under the Core PR's event (AGENTS.md §45).
    ("event-name: ''", "event-name: push"),
    ("event-ref: ''", "event-ref: refs/heads/${{ matrix.branch }}"),
    # A leg is never a merge group; its baseline is the first parent.
    ("${{ github.event.merge_group.base_sha }}", "''"),
    # github.event.before is the Core push that triggered the workflow, not
    # the consumer revision a leg checks out. A leg has no push predecessor,
    # so the expense fetch stays empty instead of reading the Core event.
    ("${{ github.event.before || '' }}", "''"),
    # The judge's condition matches the Core pull_request that launched the
    # canary. A leg's checkout is the consumer's main head, not a merge
    # commit, so that judgment is PR_SCOPE_UNKNOWN for every repository.
    # The step stays in the leg and does not run. Its pin names the
    # evaluated revision; a leg has no Core revision to fetch.
    (
        "${{ github.event_name == 'workflow_call' && github.workflow_sha || github.sha }}",
        "''",
    ),
    (
        "        if: >-\n"
        "          github.event_name == 'pull_request'\n"
        "          || github.event_name == 'merge_group'\n"
        "          || github.event_name == 'push'\n"
        "          || steps.runtime.outputs.event-class == 'pull_request'\n"
        "          || steps.runtime.outputs.event-class == 'merge'\n"
        "          || steps.runtime.outputs.event-class == 'push'",
        "        if: false",
    ),
)
TARGET_SHAPED = re.compile(
    r"github\.(repository|sha|ref|head_ref|base_ref|event\.(?!_)[a-z_.]+)\b"
)

HEADER = """\
# GENERATED by tools/promotion_canary.py render from org-ci.yml and
# central-semgrep.yml. Do not edit: change either (or the tool) and run `python3 tools/promotion_canary.py
# render` with origin/main fetched. Core's unit tests
# (tests/workflows/test_promotion_canary.py, in self-ci) fail while this file
# and org-ci.yml disagree.
#
# Core's promotion canary (AGENTS.md §40). On a Core pull request that changes
# what the required check (org-ci.yml's `analyze` job) or the nightly Semgrep
# scan (central-semgrep.yml, docs/semgrep-runs-nightly.md) runs, every
# repository declared in .l9/promotion-canary.json is checked out at its
# current main head and judged at the pull request's pins (the `head` and
# `scan` jobs) and, when the pull request changes those steps, at the base's
# pins (the `base` and `scanbase` jobs). Each is its workflow's steps verbatim
# apart from the target identity; the scan legs are the nightly sink's scan
# without its publication, so a Semgrep change is proven before promotion.
# `Promotion canary (Core)` fails only when the pull request makes a repository
# worse. Head steps digest: @@HEAD_DIGEST@@
name: Promotion canary (Core)
# One verdict per pushed head (row 22). `make pr` pushes and then edits the
# pull request's body; that `edited` run used to cancel the push run, whose
# verdict then posted a failing check for the same head. An edit that changes
# only the title or body is now a text edit: its own concurrency group (it
# never cancels the push run), no plan or legs, and a verdict job under
# another name that judges nothing, so it never posts a `Promotion canary
# (Core)` check, failing or skipped. Every other `edited` event, a base
# retarget included, is judged in full: the merge commit, and with it the
# plan, changed.
on:
  pull_request:
    types: [opened, synchronize, reopened, edited]
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: core-promotion-canary-${{ github.event.pull_request.number || github.ref }}${{ (@@TEXT_EDIT@@) && '-text-edit' || '' }}
  cancel-in-progress: true
jobs:
  plan:
    name: Plan promotion canary
    if: ${{ !(@@TEXT_EDIT@@) }}
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    outputs:
      applies: ${{ steps.plan.outputs.applies }}
      differential: ${{ steps.plan.outputs.differential }}
      base-current: ${{ steps.plan.outputs['base-current'] }}
      head-current: ${{ steps.plan.outputs['head-current'] }}
      head-digest: ${{ steps.plan.outputs['head-digest'] }}
      base-digest: ${{ steps.plan.outputs['base-digest'] }}
      matrix: ${{ steps.plan.outputs.matrix }}
      admitted: ${{ steps.plan.outputs.admitted }}
      scan: ${{ steps.plan.outputs.scan }}
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.2.0
        with:
          fetch-depth: 0
          persist-credentials: false
      - id: plan
        name: Plan the canary legs
        env:
          FORCE: ${{ github.event_name == 'workflow_dispatch' && '--force' || '' }}
          L9_CANARY_TOKEN: ${{ github.token }}
          L9_CANARY_PRIVATE_TOKEN: ${{ secrets.L9_GITHUB_READ_TOKEN }}
        run: |
          set -euo pipefail
          # shellcheck disable=SC2086
          python3 -I tools/promotion_canary.py plan ${FORCE}
      - name: Upload the pull request's SDK provisioner for the admitted leg
        if: steps.plan.outputs.admitted != ''
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: @@ADMISSION_ARTIFACT@@
          path: |
            .github/actions/provision-sdk/
            .l9/sdk-compatibility.yaml
          include-hidden-files: true
          if-no-files-found: error
          retention-days: 1

"""

LEG = """\
  @@SIDE@@:
    name: Canary ${{ matrix.repository }}@@TITLE@@
    needs: @@NEEDS@@
    if: @@IF@@
    runs-on: ubuntu-24.04
    timeout-minutes: @@TIMEOUT@@
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}
    steps:
@@STEPS@@
      # --- canary record (not part of org-ci.yml) ---
      - name: Record the canary leg
        if: always()
        env:
          SIDE: @@SIDE@@
          DIGEST: @@DIGEST@@
          JOB_STATUS: ${{ job.status }}
          AGGREGATE_STATUS: ${{ steps.aggregate.outputs.status }}
          REPOSITORY_VERIFY: ${{ steps.repository_verify.outputs.status }}
          MATRIX_ID: canary-@@SIDE@@-${{ matrix.slug }}
          SLUG: ${{ matrix.slug }}
          REPOSITORY: ${{ matrix.repository }}
          REVISION: ${{ matrix.revision }}
        run: |
          set -euo pipefail
          mkdir -p "${RUNNER_TEMP}/promotion-canary"
          python3 -I - <<'PY'
          import json
          import os
          from pathlib import Path

          env = os.environ
          blocking = set()
          runtime = Path(".l9/runtime", env["MATRIX_ID"])
          for path in sorted(runtime.rglob("gate-result.json")):
              try:
                  result = json.loads(path.read_text(encoding="utf-8"))
                  blocking.update(result["blocking_finding_ids"])
              except (OSError, ValueError, KeyError, TypeError):
                  pass
          verify = env.get("REPOSITORY_VERIFY", "")
          aggregate = env.get("AGGREGATE_STATUS", "")
          if env["JOB_STATUS"] == "cancelled":
              # Superseded by a newer run, or over the leg's time limit.
              status = "cancelled"
          elif aggregate == "incomplete":
              # Before the job status: an advisory (nightly) scan that could
              # not conclude ends its job green.
              status = "incomplete"
          elif env["JOB_STATUS"] == "success" and aggregate == "fail":
              # An advisory run whose findings did not fail the job.
              status = "findings"
          elif env["JOB_STATUS"] == "success":
              status = "pass"
          elif verify not in ("", "pass", "not_applicable"):
              status = "verification"
          elif aggregate == "fail":
              status = "findings"
          elif aggregate == "pass" and verify in ("pass", "not_applicable"):
              # Verified and analysed, then a later gate step failed.
              status = "enforcement"
          else:
              # The job failed before any verdict: infrastructure, not the
              # repository.
              status = "error"
          record = {
              "schema": "l9.promotion-canary-leg/v2",
              "side": env["SIDE"],
              "digest": env["DIGEST"],
              "repository": env["REPOSITORY"],
              "revision": env["REVISION"],
              "status": status,
              "job_status": env["JOB_STATUS"],
              "aggregate": aggregate,
              "repository_verification": verify,
              "blocking_finding_ids": sorted(blocking),
          }
          name = env["SIDE"] + "-" + env["SLUG"] + ".json"
          path = Path(env["RUNNER_TEMP"], "promotion-canary", name)
          path.write_text(json.dumps(record, indent=2) + "\\n", encoding="utf-8")
          print(json.dumps(record))
          PY

      - name: Upload the canary leg record
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: promotion-canary-@@SIDE@@-${{ matrix.slug }}
          path: ${{ runner.temp }}/promotion-canary/@@SIDE@@-${{ matrix.slug }}.json
          # A leg that cannot write its record fails its job, and the verdict
          # then finds no record and blocks.
          if-no-files-found: error
          retention-days: 14

"""

ADMITTED_IF = (
    "needs.plan.outputs.applies == 'true' && needs.plan.outputs['head-current'] "
    "== 'true' && needs.plan.outputs.admitted != ''"
)
HEAD_IF = (
    "needs.plan.outputs.applies == 'true' && needs.plan.outputs['head-current'] "
    "== 'true'"
)
BASE_IF = (
    "needs.plan.outputs.applies == 'true' && needs.plan.outputs.differential == "
    "'true' && needs.plan.outputs['base-current'] == 'true'"
)

# A reusable job org-ci.yml runs ahead of `analyze` is a sibling job of each
# leg, as it is a sibling of `analyze` in org-ci.yml: its own steps fail its own
# job, never the leg's, and nothing in the canary sets continue-on-error. A
# matrix job's `needs.<job>.result` folds every repository's leg into one, so
# each sibling hands its own leg its result as an artifact named by side, job
# and slug. The leg reads it as `steps.<job>__result.outputs.result`, which
# stands for org-ci.yml's `needs.<job>.result`; a missing result reads as
# failure, never as success.
SIBLING = """\
  @@JOB@@:
    name: Canary ${{ matrix.repository }} @@NAME@@ job@@TITLE@@
    needs: plan
    if: @@IF@@
    runs-on: ubuntu-24.04
    timeout-minutes: @@TIMEOUT@@
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}
    steps:
@@STEPS@@
      - name: Record the @@NAME@@ job result
        if: always()
        env:
          JOB_STATUS: ${{ job.status }}
        run: |
          set -euo pipefail
          mkdir -p "${RUNNER_TEMP}/canary-job"
          printf '%s\\n' "${JOB_STATUS}" > "${RUNNER_TEMP}/canary-job/@@NAME@@.result"
      - name: Upload the @@NAME@@ job result
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: canary-job-@@SIDE@@-@@NAME@@-${{ matrix.slug }}
          path: ${{ runner.temp }}/canary-job/@@NAME@@.result
          if-no-files-found: error
          retention-days: 1

"""

SIBLING_RESULT = """\
      # --- the @@NAME@@ job's result for this repository (not part of org-ci.yml) ---
      - name: Download the @@NAME@@ job result
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        with:
          # A pattern, not a name: a missing result downloads nothing (a name
          # would fail the step), and the next step reads nothing as failure.
          pattern: canary-job-@@SIDE@@-@@NAME@@-${{ matrix.slug }}
          path: ${{ runner.temp }}/canary-job
          merge-multiple: true
      - name: Result of the @@NAME@@ job
        id: @@NAME@@__result
        run: |
          set -euo pipefail
          file="${RUNNER_TEMP}/canary-job/@@NAME@@.result"
          result=""
          if [ -f "${file}" ]; then
            result="$(tr -d '[:space:]' < "${file}")"
          fi
          case "${result}" in
            success|failure|cancelled) ;;
            *) result=failure ;;
          esac
          echo "@@NAME@@ job: ${result}"
          echo "result=${result}" >> "$GITHUB_OUTPUT"
"""

VERDICT = """\
  verdict:
    name: ${{ (@@TEXT_EDIT@@) && 'Promotion canary (text edit, no verdict)' || 'Promotion canary (Core)' }}
    needs: [plan, head, scan, admitted, base, scanbase]
    # Runs whatever the plan and legs concluded, a cancelled run included (a
    # job skipped by `if:` reports success on a required check, so the verdict
    # decides itself). It is the only gate: a leg is red exactly when its own
    # org-ci run is red, and the verdict judges that against the base pins. A
    # leg job that was cancelled or did not run when planned is incomplete
    # and blocks. A superseded run's verdict fails; the newer run's verdict,
    # which always follows, is the check that counts.
    if: ${{ always() }}
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.2.0
        with:
          persist-credentials: false
      - name: Download the canary leg records
        if: needs.plan.outputs.applies == 'true'
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        with:
          pattern: promotion-canary-*
          path: ${{ runner.temp }}/promotion-canary
          merge-multiple: true
      - name: Judge the canary
        env:
          L9_CANARY_PLAN_RESULT: ${{ needs.plan.result }}
          L9_CANARY_TEXT_EDIT: ${{ @@TEXT_EDIT@@ }}
          L9_CANARY_APPLIES: ${{ needs.plan.outputs.applies }}
          L9_CANARY_DIFFERENTIAL: ${{ needs.plan.outputs.differential }}
          L9_CANARY_BASE_CURRENT: ${{ needs.plan.outputs['base-current'] }}
          L9_CANARY_HEAD_CURRENT: ${{ needs.plan.outputs['head-current'] }}
          L9_CANARY_HEAD_DIGEST: ${{ needs.plan.outputs['head-digest'] }}
          L9_CANARY_BASE_DIGEST: ${{ needs.plan.outputs['base-digest'] }}
          L9_CANARY_MATRIX: ${{ needs.plan.outputs.matrix }}
          L9_CANARY_ADMITTED: ${{ needs.plan.outputs.admitted }}
          L9_CANARY_SCAN: ${{ needs.plan.outputs.scan }}
          L9_CANARY_RECORDS: ${{ runner.temp }}/promotion-canary
          L9_CANARY_HEAD_RESULT: ${{ needs.head.result }}
          L9_CANARY_BASE_RESULT: ${{ needs.base.result }}
          L9_CANARY_SCAN_RESULT: ${{ needs.scan.result }}
          L9_CANARY_SCANBASE_RESULT: ${{ needs.scanbase.result }}
          L9_CANARY_ADMITTED_RESULT: ${{ needs.admitted.result }}
        run: |
          set -euo pipefail
          python3 -I tools/promotion_canary.py verdict
"""


# An `edited` event that changed only the title or body. Identified by what
# it changed, not by the absence of a base change alone, so an edit GitHub
# describes in any other shape is judged in full.
TEXT_EDIT = (
    "github.event.action == 'edited' && github.event.changes.base == null"
    " && (github.event.changes.title != null || github.event.changes.body != null)"
)


class CanaryError(Exception):
    """Invalid input, declaration or org-ci.yml shape (exit 2)."""


def _fill(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace(f"@@{key}@@", value)
    if "@@" in template:
        raise CanaryError("internal: an unfilled template placeholder remains")
    return template


# --- render ---------------------------------------------------------------


def analyze_steps(text: str) -> str:
    """The ``analyze`` job's ``steps:`` block of org-ci.yml, verbatim."""
    lines = text.splitlines(keepends=True)
    try:
        start = lines.index("  analyze:\n")
    except ValueError as error:
        raise CanaryError("org-ci.yml has no top-level `analyze` job") from error
    try:
        steps = next(i for i in range(start, len(lines)) if lines[i] == "    steps:\n")
    except StopIteration as error:
        raise CanaryError("the analyze job has no `steps:` block") from error
    end = next(
        (i for i in range(steps + 1, len(lines)) if re.match(r"^ {2}\S", lines[i])),
        len(lines),
    )
    body = "".join(lines[steps + 1 : end]).rstrip("\n") + "\n"
    if not body.strip():
        raise CanaryError("the analyze job's steps are empty")
    return body


def jobs_section(org_ci: str) -> str:
    """org-ci.yml from its ``jobs:`` line on: everything the run executes."""
    index = org_ci.find("\njobs:\n")
    if index < 0:
        raise CanaryError("org-ci.yml has no top-level `jobs:`")
    return org_ci[index + 1 :]


def steps_digest(org_ci: str, scan: str = "") -> str:
    """sha256 of org-ci.yml's jobs and, when given, the scan's: what the legs run."""
    text = jobs_section(org_ci) + (jobs_section(scan) if scan else "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def job_blocks(org_ci: str) -> dict[str, str]:
    """Each top-level job of org-ci.yml, by name, as its verbatim text."""
    lines = jobs_section(org_ci).splitlines(keepends=True)[1:]
    blocks: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        match = JOB_NAME.match(line)
        if match:
            current = match.group(1)
            blocks[current] = []
        elif current:
            blocks[current].append(line)
        elif line.strip() and not line.lstrip().startswith("#"):
            raise CanaryError(f"org-ci.yml jobs: unexpected line {line.strip()!r}")
    return {name: "".join(body) for name, body in blocks.items()}


def _job_key(name: str, stripped: str) -> tuple[str, str]:
    """(key, value) of a job-level line; only uses, if and with are supported."""
    key, _, value = stripped.partition(":")
    if key not in ("uses", "if", "with"):
        raise CanaryError(
            f"org-ci.yml job {name!r}: {key!r} is not supported by the canary"
        )
    return key, value.strip()


def _reusable_call(name: str, body: str) -> tuple[str, dict[str, str]]:
    """(uses, with-values) of one non-analyze org-ci job; anything else refused."""
    uses = ""
    values: dict[str, str] = {}
    section = ""
    lines = [line for line in body.splitlines() if line.strip()]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if line.startswith("    ") and not line.startswith("     "):
            section, value = _job_key(name, stripped)
            uses = value if section == "uses" else uses
        elif section == "with" and line.startswith("      "):
            key, _, value = stripped.partition(":")
            values[key.strip()] = value.strip().strip("'\"")
        elif section != "if":
            raise CanaryError(f"org-ci.yml job {name!r}: unexpected {stripped!r}")
    return uses, values


def reusable_jobs(org_ci: str) -> list[tuple[str, str, str, dict[str, str]]]:
    """(job, workflow path, pinned sha, with-values) for every non-analyze job.

    A job other than ``analyze`` must call a Core reusable workflow at a full
    sha, optionally with ``with:`` values and an ``if:``; anything else (steps,
    needs, secrets, a matrix) is refused rather than left out of the canary.
    """
    out = []
    for name, body in job_blocks(org_ci).items():
        if name == "analyze":
            continue
        uses, values = _reusable_call(name, body)
        match = REUSABLE.match(uses)
        if not match:
            raise CanaryError(
                f"org-ci.yml job {name!r} must call a Core reusable workflow at a "
                f"full sha (got {uses!r})"
            )
        out.append((name, match.group(1), match.group(2), values))
    return out


def _inputs(workflow: str) -> dict[str, str]:
    """``workflow_call`` input defaults of a reusable workflow (strings)."""
    lines = workflow.splitlines()
    try:
        start = lines.index("  workflow_call:")
        start = lines.index("    inputs:", start)
    except ValueError:
        return {}
    defaults: dict[str, str] = {}
    name = ""
    for line in lines[start + 1 :]:
        if line and not line.startswith("      "):
            break
        if (
            line.startswith("      ")
            and not line.startswith("       ")
            and line.endswith(":")
        ):
            name = line.strip()[:-1]
            defaults[name] = ""
        elif name and line.strip().startswith("default:"):
            defaults[name] = line.strip()[len("default:") :].strip().strip("'\"")
    return defaults


def _single_job(workflow: str, what: str) -> tuple[str, str, int]:
    """(steps block, default working directory, timeout) of a one-job workflow."""
    blocks = job_blocks(workflow)
    if len(blocks) != 1:
        raise CanaryError(f"{what}: the canary inlines one-job workflows only")
    (body,) = blocks.values()
    for key in ("strategy:", "services:", "container:", "needs:", "outputs:"):
        if re.search(rf"(?m)^ {{4}}{key}", body):
            raise CanaryError(f"{what}: job-level {key[:-1]} is not supported")
    lines = body.splitlines(keepends=True)
    try:
        steps = lines.index("    steps:\n")
    except ValueError as error:
        raise CanaryError(f"{what}: the job has no steps") from error
    directory = re.search(r"(?m)^ {8}working-directory: (.+)$", "".join(lines[:steps]))
    timeout = re.search(r"(?m)^ {4}timeout-minutes: (\d+)$", body)
    return (
        "".join(lines[steps + 1 :]).rstrip("\n") + "\n",
        directory.group(1).strip() if directory else ".",
        int(timeout.group(1)) if timeout else 360,
    )


def _expression_value(value: str) -> str:
    if value in ("true", "false") or re.fullmatch(r"-?\d+", value):
        return value
    return "'" + value.replace("'", "''") + "'"


CHECKOUT_IDENTITY = (
    "          repository: ${{ matrix.repository }}\n"
    "          ref: ${{ matrix.revision }}\n"
    "          token: ${{ matrix.private && secrets.L9_GITHUB_READ_TOKEN"
    " || github.token }}\n"
)
INPUT_EXPRESSION = re.compile(r"\$\{\{\s*inputs\.([a-z0-9-]+)\s*\}\}")
INPUT_BARE = re.compile(r"\binputs\.([a-z0-9-]+)\b")
NOT_RETARGETED = re.compile(r"\b(inputs|needs)\.|\bsecrets\.(?!L9_GITHUB_READ_TOKEN\b)")


def _substitute(text: str, inputs: dict[str, str], what: str) -> str:
    """Resolve ``inputs.*`` and the event identity for a push of the head."""

    def value(match: re.Match[str], expression: bool) -> str:
        key = match.group(1)
        if key not in inputs:
            raise CanaryError(f"{what}: input {key!r} has no value")
        return _expression_value(inputs[key]) if expression else inputs[key] or "''"

    text = INPUT_EXPRESSION.sub(lambda m: value(m, False), text)
    text = INPUT_BARE.sub(lambda m: value(m, True), text)
    # A main head has no pull request range: describe a push of that commit.
    text = text.replace("${{ github.event_name }}", "push")
    text = re.sub(r"\$\{\{\s*github\.event\.[a-z_.]+\s*\}\}", "''", text)
    text = text.replace("${{ github.sha }}", MATRIX_REVISION)
    return text.replace("${{ github.repository }}", "${{ matrix.repository }}")


def _isolate_step(item: str, name: str, number: int, directory: str) -> tuple[str, str]:
    """(step text, step id): prefixed id, working directory, and a checkout
    retargeted to the evaluated repository. No continue-on-error: the step runs
    in the job's own sibling job, so a failure fails that job only."""
    own = re.search(r"(?m)^ {8}id: ([A-Za-z0-9_-]+)$", item)
    step_id = f"{name}__{own.group(1) if own else f's{number}'}"
    if own:
        item = item.replace(own.group(0), f"        id: {step_id}")
    first, _, rest = item.partition("\n")
    extra = "" if own else f"        id: {step_id}\n"
    runs_here = re.search(r"(?m)^ {8}run:", item) and not re.search(
        r"(?m)^ {8}working-directory:", item
    )
    if runs_here and directory != ".":
        extra += f"        working-directory: {directory}\n"
    item = first + "\n" + extra + rest
    if re.search(
        r"(?m)^ {6}- uses: actions/checkout@|^ {8}uses: actions/checkout@", item
    ):
        if re.search(r"(?m)^ {8}with:$", item):
            item = re.sub(
                r"(?m)^( {8}with:\n)",
                lambda m: m.group(1) + CHECKOUT_IDENTITY,
                item,
                count=1,
            )
        else:
            item = item.rstrip("\n") + "\n        with:\n" + CHECKOUT_IDENTITY
    return (item if item.endswith("\n") else item + "\n"), step_id


def inline_job(name: str, workflow: str, values: dict[str, str], what: str) -> str:
    """A reusable workflow's job as the steps of its canary sibling job,
    retargeted and isolated."""
    steps, directory, _ = _single_job(workflow, what)
    inputs = {**_inputs(workflow), **values}
    directory = _substitute(directory, inputs, what)
    steps = _substitute(steps, inputs, what)
    items = [item for item in re.split(r"(?m)^(?= {6}- )", steps) if item.strip()]
    isolated = [
        _isolate_step(item, name, number, directory)
        for number, item in enumerate(items)
    ]
    ids = [step_id for _, step_id in isolated]
    body = "".join(text for text, _ in isolated)
    for step in ids:
        bare_id = step.split("__", 1)[1]
        body = re.sub(rf"\bsteps\.{re.escape(bare_id)}\.", f"steps.{step}.", body)
    left = [
        line.strip()
        for line in body.splitlines()
        if not line.lstrip().startswith("#")
        and (TARGET_SHAPED.search(line) or NOT_RETARGETED.search(line))
    ]
    if left:
        raise CanaryError(f"{what}: references the canary cannot retarget: {left}")
    return body


Provider = Any  # Callable[[str, str, str, dict[str, str]], str]


def git_provider(root: Path) -> Provider:
    """Inline a reusable job from the workflow at its pinned sha (needs history)."""

    def provide(name: str, path: str, sha: str, values: dict[str, str]) -> str:
        if not SHA.match(sha) or not re.fullmatch(
            r"\.github/workflows/[A-Za-z0-9._-]+\.ya?ml", path
        ):
            raise CanaryError(f"invalid reusable workflow reference {path}@{sha}")
        workflow = _git(root, "show", f"{sha}:{path}")
        return inline_job(name, workflow, values, f"{path}@{sha}")

    return provide


def embedded_provider(workflow: str) -> Provider:
    """Inline a reusable job from the blocks a committed workflow carries."""

    def provide(name: str, path: str, sha: str, values: dict[str, str]) -> str:
        key = f"{name} {path}@{sha}"
        begin = INLINE_BEGIN.format(key=key)
        start = workflow.find(begin)
        end = workflow.find(INLINE_END.format(key=key), start)
        if start < 0 or end < 0:
            raise CanaryError(f"{CANARY} carries no inlined {key}; run render")
        return workflow[start + len(begin) : end]

    return provide


def retarget(steps: str, side: str = "head") -> str:
    """Replace the target identity; refuse anything target-shaped that is left."""
    for old, new in REPLACEMENTS:
        if old not in steps:
            raise CanaryError(f"org-ci.yml no longer contains {old!r}; update the tool")
        steps = steps.replace(old, new.replace("@@SIDE@@", side))
    for old, new in OPTIONAL_REPLACEMENTS:
        steps = steps.replace(old, new)
    left = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(steps.splitlines(), 1)
        if not line.lstrip().startswith("#") and TARGET_SHAPED.search(line)
    ]
    if left:
        raise CanaryError(
            "target-shaped references the canary cannot retarget: " + "; ".join(left)
        )
    return steps


def _action_inputs(action: str) -> dict[str, str]:
    """The provision-sdk action's inputs and their defaults ("" when none)."""
    section = re.search(r"^inputs:\n((?:(?: {2}[^\n]*)?\n)*)", action, re.M)
    if not section:
        raise CanaryError(f"{PROVISION_ACTION} declares no inputs")
    inputs: dict[str, str] = {}
    name = ""
    for line in section.group(1).splitlines():
        if match := re.fullmatch(r" {2}([a-z][a-z0-9-]*):", line):
            name = match.group(1)
            inputs[name] = ""
        elif name and (match := re.fullmatch(r" {4}default: (.*)", line)):
            inputs[name] = match.group(1).strip().strip('"')
    return inputs


def _composite_steps(action: str) -> list[str]:
    """The provision-sdk action's composite steps, each as its own text."""
    if not re.search(r"^runs:\n {2}using: composite\n {2}steps:\n", action, re.M):
        raise CanaryError(f"{PROVISION_ACTION} is not a composite action")
    body = action.split("  steps:\n", 1)[1]
    steps = re.split(r"^(?= {4}- )", body, flags=re.M)
    return [step for step in steps if step.strip()]


def _sdk_step(analyze: str) -> tuple[str, str, dict[str, str]]:
    """org-ci.yml's provision-sdk step: its text, its `if:` and its `with:`."""
    found = SDK_STEP.findall(analyze)
    if len(found) != 1:
        raise CanaryError("org-ci.yml analyze must hold exactly one `id: sdk` step")
    step = found[0]
    if "/.github/actions/provision-sdk@" not in step:
        raise CanaryError("org-ci.yml's `id: sdk` step no longer runs provision-sdk")
    condition = re.search(r"^ {8}if: (.+)$", step, re.M)
    values = re.findall(
        r"^ {10}([a-z][a-z0-9-]*): (.+)$", step.split("with:\n", 1)[1], re.M
    )
    return step, condition.group(1) if condition else "", dict(values)


def _check_outputs(action: str) -> None:
    """Refuse a provision-sdk whose outputs are not its `provision` step's."""
    section = re.search(r"^outputs:\n((?: {2}[^\n]*\n)*)", action, re.M)
    outputs = re.findall(
        r"^ {2}([a-z][a-z0-9-]*):\n(?: {4}(?!value:)[^\n]*\n)* {4}value: (.+)$",
        section.group(1) if section else "",
        re.M,
    )
    if not outputs or any(
        value != f"${{{{ steps.provision.outputs.{name} }}}}" for name, value in outputs
    ):
        raise CanaryError(
            "provision-sdk's outputs must map one-to-one onto its `provision` step"
        )


def _inline_composite(item: str, inputs: dict[str, str], guard: str) -> str:
    """One composite step as a job step of the admitted leg."""
    text = item.replace(
        "${{ github.action_path }}", f"{ADMITTED_CORE}/.github/actions/provision-sdk"
    )
    for name, value in inputs.items():
        text = text.replace(f"${{{{ inputs.{name} }}}}", value)
    if re.search(r"\binputs\.", text):
        raise CanaryError("provision-sdk reads an input the canary cannot map")
    text = re.sub(r"^ {4}- id: provision$", "    - id: sdk", text, flags=re.M)
    lines = ["  " + line if line else line for line in text.splitlines()]
    if guard:
        lines.insert(1, guard)
    return "\n".join(lines).rstrip("\n") + "\n"


def admit(analyze: str, action: str) -> str:
    """The analyze steps with the provision-sdk step run from the pull request.

    The pinned provision-sdk refuses a revision its own compatibility manifest
    does not admit, so the admitted leg runs the pull request's provision-sdk:
    the plan uploads it with that manifest, this leg downloads it outside the
    workspace and runs its composite steps inline, with `sdk-revision` set to
    the revision the pull request admits. Everything else is the head leg.
    """
    step, condition, values = _sdk_step(analyze)
    inputs = _action_inputs(action)
    unknown = sorted(set(values) - set(inputs))
    if unknown:
        raise CanaryError(f"provision-sdk has no input(s) {unknown}")
    inputs.update(values)
    inputs["sdk-revision"] = "${{ needs.plan.outputs.admitted }}"
    _check_outputs(action)
    guard = f"        if: {condition}" if condition else ""
    replaced = (
        "      # --- admitted SDK revision: provision-sdk from the pull request ---\n"
        "      - name: Download the pull request's SDK provisioner\n"
        + (guard + "\n" if guard else "")
        + "        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0\n"
        "        with:\n"
        f"          name: {ADMISSION_ARTIFACT}\n"
        f"          path: {ADMITTED_CORE}\n"
    )
    for item in _composite_steps(action):
        replaced += _inline_composite(item, inputs, guard)
    if "      - id: sdk\n" not in replaced:
        raise CanaryError("provision-sdk has no `provision` step")
    return analyze.replace(step, replaced + "\n")


def leg_job(
    side: str,
    org_ci: str,
    provider: Provider,
    admission: str = "",
    digest: str = "",
    nightly: bool = False,
) -> str:
    """One leg: a sibling job per reusable job of the source workflow (org-ci.yml
    or the scan), then its ``analyze`` (which reads each sibling's result for its
    repository), plus the record. ``digest`` is what the leg records as the steps
    it ran: both families' digest, so one plan judges them together."""
    titles = {
        "base": " (base pins)",
        "admitted": " (admitted SDK, nightly scan)",
        SCAN_SIDE: " (nightly scan)",
        SCAN_BASE_SIDE: " (nightly scan, base pins)",
    }
    conditions = {"base": BASE_IF, SCAN_BASE_SIDE: BASE_IF, "admitted": ADMITTED_IF}
    title = titles.get(side, "")
    condition = conditions.get(side, HEAD_IF)
    siblings = ""
    results = ""
    needs = ["plan"]
    analyze = retarget(analyze_steps(org_ci), side)
    if nightly:
        if analyze.count(EVENT_LINE) != 1:
            raise CanaryError(
                f"the scan's steps no longer contain {EVENT_LINE.strip()!r} once"
            )
        analyze = analyze.replace(EVENT_LINE, NIGHTLY_EVENT_LINE)
    for name, path, sha, values in reusable_jobs(org_ci):
        key = f"{name} {path}@{sha}"
        block = provider(name, path, sha, values)
        job = f"{side}__{name}"
        needs.append(job)
        siblings += _fill(
            SIBLING,
            JOB=job,
            NAME=name,
            SIDE=side,
            TITLE=title,
            IF=condition,
            TIMEOUT=str(SIBLING_TIMEOUT),
            STEPS=(
                INLINE_BEGIN.format(key=key) + block + INLINE_END.format(key=key)
            ).rstrip("\n"),
        )
        results += _fill(SIBLING_RESULT, NAME=name, SIDE=side)
        pattern = rf"\$\{{\{{\s*needs\.{re.escape(name)}\.result\s*\}}\}}"
        analyze = re.sub(
            pattern, f"${{{{ steps.{name}__result.outputs.result }}}}", analyze
        )
    if re.search(r"\bneeds\.", analyze):
        raise CanaryError(
            "org-ci.yml analyze reads a `needs.` value the canary cannot map"
        )
    if side == "admitted":
        analyze = admit(analyze, admission)
    if len(needs) > 1:
        # org-ci.yml runs analyze whatever its sibling jobs concluded, then
        # enforces their results; a skipped leg would read as passing.
        needs_text = "[" + ", ".join(needs) + "]"
        condition = f"${{{{ !cancelled() && ({condition}) }}}}"
    else:
        needs_text = "plan"
    return siblings + _fill(
        LEG,
        SIDE=side,
        TITLE=title,
        NEEDS=needs_text,
        IF=condition,
        TIMEOUT=str(LEG_TIMEOUT),
        STEPS=(results + analyze).rstrip("\n"),
        DIGEST=digest or steps_digest(org_ci),
    )


def base_block(base_org_ci: str, provider: Provider, base_scan: str = "") -> str:
    """The embedded base legs, between their markers: org-ci.yml's and, when
    given, the scan's at the base pins. Until the scan has its own workflow at
    the base, the base's org-ci.yml is that scan (it ran Semgrep itself)."""
    digest = steps_digest(base_org_ci, base_scan)
    scan = (
        leg_job(SCAN_BASE_SIDE, base_scan, provider, digest=digest, nightly=True)
        if base_scan
        else ""
    )
    return (
        BASE_BEGIN
        + leg_job("base", base_org_ci, provider, digest=digest)
        + scan
        + BASE_END
    )


def embedded_base(workflow: str) -> str:
    """The base leg block a committed workflow carries."""
    start = workflow.find(BASE_BEGIN)
    end = workflow.find(BASE_END)
    if start < 0 or end < start:
        raise CanaryError(f"{CANARY} carries no embedded base leg")
    return workflow[start : end + len(BASE_END)]


def render(
    org_ci: str, base: str, provider: Provider, admission: str, scan: str = ""
) -> str:
    """The workflow for this ``org-ci.yml`` and scan, the provision-sdk action
    (``admission``) and an embedded base leg block. Without a scan (an older
    tree), the admitted leg runs org-ci.yml, which then provisioned the SDK."""
    digest = steps_digest(org_ci, scan)
    legs = leg_job("head", org_ci, provider, digest=digest)
    if scan:
        legs += leg_job(SCAN_SIDE, scan, provider, digest=digest, nightly=True)
    legs += leg_job(
        "admitted",
        scan or org_ci,
        provider,
        admission,
        digest=digest,
        nightly=bool(scan),
    )
    return (
        _fill(
            HEADER,
            HEAD_DIGEST=digest,
            ADMISSION_ARTIFACT=ADMISSION_ARTIFACT,
            TEXT_EDIT=TEXT_EDIT,
        )
        + legs
        + base
        + "\n"
        + _fill(VERDICT, TEXT_EDIT=TEXT_EDIT)
    )


def scan_source(read: Any) -> str:
    """The scan workflow from ``read(path)``, or the org-ci.yml that still ran
    Semgrep itself (a base before the scan had its own workflow)."""
    try:
        return read(SCAN)
    except CanaryError:
        return read(ORG_CI)


def core_action_paths(org_ci: str) -> list[str]:
    names = sorted(set(CORE_ACTION.findall(org_ci)))
    return [f".github/actions/{name}/" for name in names]


def _cmd_render(args: argparse.Namespace) -> int:
    org_ci = (args.root / ORG_CI).read_text(encoding="utf-8")
    scan = (args.root / SCAN).read_text(encoding="utf-8")
    admission = read_admission(args.root)
    target = args.root / CANARY
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        head_part = current.split(BASE_BEGIN, 1)[0]
        expected = render(
            org_ci,
            embedded_base(current),
            embedded_provider(head_part),
            admission,
            scan,
        )
        if current != expected:
            print(
                f"::error::{CANARY} is stale against {ORG_CI}; run "
                "python3 tools/promotion_canary.py render",
                file=sys.stderr,
            )
            return 1
        print(f"{CANARY} matches {ORG_CI}")
        return 0
    main_org_ci = _git(args.root, "show", "origin/main:.github/workflows/org-ci.yml")
    main_scan = scan_source(lambda path: _git(args.root, "show", f"origin/main:{path}"))
    provider = git_provider(args.root)
    base = base_block(main_org_ci, provider, main_scan)
    target.write_text(render(org_ci, base, provider, admission, scan), encoding="utf-8")
    print(f"wrote {CANARY} (base leg from origin/main)")
    return 0


def read_admission(root: Path) -> str:
    try:
        return (root / PROVISION_ACTION).read_text(encoding="utf-8")
    except OSError as error:
        raise CanaryError(f"{PROVISION_ACTION} unreadable: {error}") from error


def admitted_revision(source: str, compatibility: str) -> str:
    """The SDK revision this tree admits by default but the scan does not run.

    ``source`` is the workflow that provisions the SDK: the scan
    (central-semgrep.yml), or an older org-ci.yml that still did. ``""`` when
    its fallback already is the admitted default. An
    admission-only pull request (the first of the two promotion steps) changes
    the default without org-ci.yml, so its head and base legs both run the
    current pin; this is the revision the admitted leg runs instead.
    """
    default = DEFAULT_REVISION.findall(compatibility)
    fallback = SDK_FALLBACK.findall(source)
    if len(default) != 1:
        raise CanaryError(f"{SDK_COMPATIBILITY} must declare one default revision")
    if len(set(fallback)) != 1:
        raise CanaryError(
            "the scan (central-semgrep.yml) must carry one sdk-revision fallback"
        )
    return "" if default[0] == fallback[0] else default[0]


# --- plan -----------------------------------------------------------------


def load_declaration(root: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((root / DECLARATION).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CanaryError(f"{DECLARATION} unreadable: {error}") from error
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise CanaryError(f"{DECLARATION} must declare schema {SCHEMA}")
    entries = data.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise CanaryError(f"{DECLARATION} must list repositories")
    seen: set[str] = set()
    out = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"repository", "branch", "private"}
            or not isinstance(entry["repository"], str)
            or not REPOSITORY.match(entry["repository"])
            or not isinstance(entry["branch"], str)
            or not BRANCH.match(entry["branch"])
            or ".." in entry["branch"]
            or not isinstance(entry["private"], bool)
        ):
            raise CanaryError(f"{DECLARATION} has an invalid entry: {entry!r}")
        key = entry["repository"].lower()
        if key in seen:
            raise CanaryError(f"{DECLARATION} lists {entry['repository']} twice")
        seen.add(key)
        out.append(dict(entry))
    return out


def _git(root: Path, *args: str) -> str:
    """Run git with a constant, Core-authored argument list."""
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise CanaryError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def changed_paths(root: Path) -> list[str]:
    """Paths the checked-out change touches: ``HEAD`` against its first parent.

    A pull request is checked out as GitHub's merge commit, whose first parent
    is the base tip, so this is exactly what merging would change. No revision
    is taken from outside: the command line is constant.
    """
    out = _git(root, "diff", "--name-only", "HEAD^1", "HEAD", "--")
    return [line for line in out.splitlines() if line]


def base_org_ci(root: Path) -> str:
    """org-ci.yml as the base tip (``HEAD^1``) has it."""
    return _git(root, "show", "HEAD^1:.github/workflows/org-ci.yml")


def base_scan(root: Path) -> str:
    """The scan as the base tip (``HEAD^1``) has it (see ``scan_source``)."""
    return scan_source(lambda path: _git(root, "show", f"HEAD^1:{path}"))


def touched(paths: Sequence[str], watched: Sequence[str]) -> list[str]:
    return sorted(
        p
        for p in paths
        if any(p == w or (w.endswith("/") and p.startswith(w)) for w in watched)
    )


def _get(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def resolve(entry: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    token = env.get(
        "L9_CANARY_PRIVATE_TOKEN" if entry["private"] else "L9_CANARY_TOKEN", ""
    )
    api = "https://api.github.com/repos/" + entry["repository"]
    branch = urllib.parse.quote(entry["branch"], safe="")
    try:
        revision = _get(f"{api}/commits/{branch}", token)["sha"]
    except (urllib.error.URLError, KeyError, ValueError, TypeError) as error:
        raise CanaryError(
            f"cannot resolve {entry['repository']}@{entry['branch']}: {error}"
        ) from error
    if not SHA.match(str(revision)):
        raise CanaryError(f"{entry['repository']} head is not a full revision")
    slug = re.sub(r"[^a-z0-9]+", "-", entry["repository"].lower()).strip("-")
    return {
        "repository": entry["repository"],
        "branch": entry["branch"],
        "private": entry["private"],
        "revision": revision,
        "slug": slug,
    }


def _write_output(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT", "")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(lines)
    else:
        sys.stdout.write(lines)


def _cmd_plan(args: argparse.Namespace) -> None:
    entries = load_declaration(args.root)
    org_ci = (args.root / ORG_CI).read_text(encoding="utf-8")
    scan = (args.root / SCAN).read_text(encoding="utf-8")
    base = base_org_ci(args.root)
    base_scan_text = base_scan(args.root)
    head_digest = steps_digest(org_ci, scan)
    base_digest = steps_digest(base, base_scan_text)
    differential = head_digest != base_digest
    workflow = (args.root / CANARY).read_text(encoding="utf-8")
    provider = git_provider(args.root)
    embedded = embedded_base(workflow)
    head_current = workflow == render(
        org_ci, embedded, provider, read_admission(args.root), scan
    )
    try:
        compatibility = (args.root / SDK_COMPATIBILITY).read_text(encoding="utf-8")
    except OSError as error:
        raise CanaryError(f"{SDK_COMPATIBILITY} unreadable: {error}") from error
    admitted = admitted_revision(scan, compatibility)
    base_current = differential and embedded == base_block(
        base, provider, base_scan_text
    )
    watched = [*STATIC_PATHS, *core_action_paths(org_ci + scan)]
    reasons = ["dispatch"] if args.force else touched(changed_paths(args.root), watched)
    outputs = {
        "applies": "false",
        "differential": "true" if differential else "false",
        "base-current": "true" if base_current else "false",
        "head-current": "true" if head_current else "false",
        "head-digest": head_digest,
        "base-digest": base_digest,
        "matrix": json.dumps({"include": []}),
        "admitted": "",
        "scan": "true",
    }
    if not reasons:
        print("No canary path changed; the canary does not apply.")
        _write_output(outputs)
        return
    print("Canary paths changed: " + ", ".join(reasons[:20]))
    if not head_current:
        print(
            "::error::The canary's inlined org-ci jobs are not what org-ci.yml pins: "
            "run python3 tools/promotion_canary.py render"
        )
    if not differential:
        print("The analyze steps are unchanged: the base result is the pull request's.")
    elif base_current:
        print("The analyze steps change: the embedded base leg is the base tip's.")
    else:
        print(
            "::error::The analyze steps change, but the embedded base leg is not "
            "the base tip's: fetch origin/main and run "
            "python3 tools/promotion_canary.py render"
        )
    if admitted:
        print(
            f"The tree admits SDK {admitted}, which org-ci.yml does not run yet: "
            "the admitted leg runs it."
        )
    include = [resolve(entry, dict(os.environ)) for entry in entries]
    for leg in include:
        print(f"{leg['repository']}@{leg['revision']}")
    outputs["applies"] = "true"
    outputs["admitted"] = admitted
    outputs["matrix"] = json.dumps({"include": include}, separators=(",", ":"))
    _write_output(outputs)


# --- verdict --------------------------------------------------------------


def read_record(records: Path, side: str, leg: dict[str, Any]) -> dict[str, Any]:
    """A leg record, or a synthetic ``missing`` one saying why."""
    try:
        record = json.loads((records / f"{side}-{leg['slug']}.json").read_text("utf-8"))
        if not isinstance(record, dict) or record.get("revision") != leg["revision"]:
            raise ValueError("revision mismatch")
        record["status"] = str(record["status"])
        record["blocking_finding_ids"] = list(record.get("blocking_finding_ids", []))
        return record
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "missing", "detail": str(error), "blocking_finding_ids": []}


def compare(
    base: dict[str, Any], head: dict[str, Any], digests: tuple[str, str]
) -> tuple[bool, str]:
    """(passed, reason) for one repository at the base pins and the PR's."""
    base_digest, head_digest = digests
    after = head["status"]
    if after == "missing":
        return False, f"no pull-request leg record ({head.get('detail', '')})"
    if head.get("digest") != head_digest:
        return False, "the pull-request leg ran other steps than the plan"
    if after in NO_VERDICT:
        return False, f"{after} at the pull request's pins"
    before = base["status"]
    if before == "missing":
        return False, f"no base-pins leg record ({base.get('detail', '')})"
    if base.get("digest") != base_digest:
        return False, "the base-pins leg did not run the base's steps"
    if before in NO_VERDICT:
        return False, f"no baseline: {before} at the base pins"
    if after == "pass":
        return True, "improved" if before != "pass" else "kept green"
    if before == "pass":
        return False, f"regression: pass -> {after}"
    if after in ("verification", "enforcement") and before != after:
        return False, f"regression: {after} now fails ({before} at the base pins)"
    new = sorted(set(head["blocking_finding_ids"]) - set(base["blocking_finding_ids"]))
    if new:
        return False, f"{len(new)} new blocking finding(s): {', '.join(new[:5])}"
    return True, "kept: already red at the base pins, nothing new"


def _plan_problem(env: dict[str, str]) -> str:
    """Why the plan cannot be judged, or "" when it can."""
    head_digest = env.get("L9_CANARY_HEAD_DIGEST", "")
    base_digest = env.get("L9_CANARY_BASE_DIGEST", "")
    differential = env.get("L9_CANARY_DIFFERENTIAL") == "true"
    if not DIGEST.match(head_digest) or not DIGEST.match(base_digest):
        return "the canary applies but its plan is incomplete"
    if differential == (head_digest == base_digest):
        return "the plan's differential flag contradicts its digests"
    admitted = env.get("L9_CANARY_ADMITTED", "")
    if admitted and not SHA.match(admitted):
        return "the plan's admitted SDK revision is not a full revision"
    if env.get("L9_CANARY_HEAD_CURRENT") != "true":
        return (
            "the canary's inlined org-ci jobs are not what org-ci.yml pins: run "
            "`python3 tools/promotion_canary.py render`, and push"
        )
    if differential and env.get("L9_CANARY_BASE_CURRENT") != "true":
        return (
            "the pull request changes the analyze steps, but its embedded base leg "
            "is not the base tip's: fetch origin/main, run "
            "`python3 tools/promotion_canary.py render`, and push"
        )
    return ""


def _planned_legs(env: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    """The planned legs, or why the verdict cannot judge them ("" when it can)."""
    try:
        legs = json.loads(env.get("L9_CANARY_MATRIX", ""))["include"]
    except (ValueError, KeyError, TypeError) as error:
        return [], f"the canary plan is unreadable: {error}"
    problem = _plan_problem(env) if legs else "the canary applies but planned no leg"
    return legs, problem


def _judge_pins(
    legs: list[dict[str, Any]],
    records: Path,
    env: dict[str, str],
    sides: tuple[str, str] = ("head", "base"),
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """Each repository at the base pins and the pull request's: rows and heads."""
    head_side, base_side = sides
    head_digest = env["L9_CANARY_HEAD_DIGEST"]
    base_digest = env["L9_CANARY_BASE_DIGEST"]
    differential = env.get("L9_CANARY_DIFFERENTIAL") == "true"
    passed = True
    rows = [
        "| Repository | Head | Base pins | Pull request pins | Verdict |",
        "|---|---|---|---|---|",
    ]
    heads = []
    for leg in legs:
        head = read_record(records, head_side, leg)
        if differential:
            base = read_record(records, base_side, leg)
        else:
            base = {**head, "digest": base_digest}
        ok, reason = compare(base, head, (base_digest, head_digest))
        passed = passed and ok
        heads.append(head)
        rows.append(
            f"| {leg['repository']} | `{leg['revision'][:12]}` | {base['status']} "
            f"| {head['status']} | {'pass' if ok else 'FAIL'}: {reason} |"
        )
    return passed, rows, heads


def _judge_admitted(
    legs: list[dict[str, Any]],
    heads: list[dict[str, Any]],
    records: Path,
    env: dict[str, str],
) -> tuple[bool, list[str]]:
    """Each repository at the admitted SDK, against the pull request's pins."""
    admitted = env["L9_CANARY_ADMITTED"]
    head_digest = env["L9_CANARY_HEAD_DIGEST"]
    passed = True
    rows = [
        "",
        f"The pull request admits SDK `{admitted[:12]}` that the scan does not "
        "run yet: judged against the pull request's pins.",
        "",
        "| Repository | Pull request pins | Admitted SDK | Verdict |",
        "|---|---|---|---|",
    ]
    for leg, head in zip(legs, heads, strict=True):
        after = read_record(records, "admitted", leg)
        ok, reason = compare(head, after, (head_digest, head_digest))
        passed = passed and ok
        rows.append(
            f"| {leg['repository']} | {head['status']} | {after['status']} "
            f"| {'pass' if ok else 'FAIL'}: {reason} |"
        )
    return passed, rows


# A leg job concludes success when its repository's org-ci run is green and
# failure when it is red; either is judged from the leg records. Any other
# result (cancelled, skipped, unknown) means planned legs did not finish.
LEG_JOB_RESULTS = ("success", "failure")


def _leg_jobs_problem(env: dict[str, str]) -> str:
    """Why the planned leg jobs cannot be judged, or "" when they can."""
    scan = env.get("L9_CANARY_SCAN") == "true"
    sides = ["head", SCAN_SIDE] if scan else ["head"]
    if env.get("L9_CANARY_DIFFERENTIAL") == "true":
        sides += ["base", SCAN_BASE_SIDE] if scan else ["base"]
    if env.get("L9_CANARY_ADMITTED", ""):
        sides.append("admitted")
    for side in sides:
        result = env.get(f"L9_CANARY_{side.upper()}_RESULT", "")
        if result not in LEG_JOB_RESULTS:
            return (
                f"incomplete: the {side} legs concluded {result or 'nothing'}, "
                "not a judged run"
            )
    return ""


def judge(env: dict[str, str]) -> tuple[bool, list[str]]:
    """(passed, report lines) from the verdict job's environment."""
    if env.get("L9_CANARY_TEXT_EDIT") == "true":
        # Reported under another check name: the push run judges this head.
        return True, [
            "A title or body edit: no verdict; the push run judges this head."
        ]
    plan_result = env.get("L9_CANARY_PLAN_RESULT", "")
    if plan_result != "success":
        return False, [f"FAIL the canary plan did not succeed ({plan_result or '?'})"]
    if env.get("L9_CANARY_APPLIES") != "true":
        return True, ["No canary path changed: not applicable."]
    legs, problem = _planned_legs(env)
    if problem:
        return False, [f"FAIL {problem}"]
    problem = _leg_jobs_problem(env)
    if problem:
        return False, [f"FAIL {problem}"]
    records = Path(env.get("L9_CANARY_RECORDS", ""))
    passed, rows, heads = _judge_pins(legs, records, env)
    mode = (
        "The pull request changes the analyze steps: judged against the base pins."
        if env.get("L9_CANARY_DIFFERENTIAL") == "true"
        else "The analyze steps are unchanged: the base result is the pull request's."
    )
    report = [mode, "", "Required check (org-ci.yml):", "", *rows]
    if env.get("L9_CANARY_SCAN") == "true":
        scan_passed, scan_rows, heads = _judge_pins(
            legs, records, env, (SCAN_SIDE, SCAN_BASE_SIDE)
        )
        passed = passed and scan_passed
        report += ["", "Nightly Semgrep scan (central-semgrep.yml):", "", *scan_rows]
    if env.get("L9_CANARY_ADMITTED", ""):
        admitted_passed, admitted_rows = _judge_admitted(legs, heads, records, env)
        passed = passed and admitted_passed
        report += admitted_rows
    return passed, report


def _cmd_verdict(_args: argparse.Namespace) -> int:
    passed, report = judge(dict(os.environ))
    text = "\n".join(report)
    print(text)
    for line in report:
        if "FAIL" in line:
            print("::error::" + line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("### Promotion canary (Core)\n\n" + text + "\n")
            stream.write(f"\nVerdict: {'pass' if passed else 'fail'}\n")
    return 0 if passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promotion_canary")
    parser.add_argument("--root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--check", action="store_true")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--force", action="store_true")
    sub.add_parser("verdict")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "plan":
            _cmd_plan(args)
            return 0
        if args.command == "render":
            return _cmd_render(args)
        return _cmd_verdict(args)
    except CanaryError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
