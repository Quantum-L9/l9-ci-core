# Nightly deep consumer contract

Status: contract for consumers. Nothing in a consumer repository changes
because of this document. Core admits each entry point in
[`.github/nightly/deep-entrypoints.json`](../.github/nightly/deep-entrypoints.json).

## Operator requirement (2026-11-20)

Expensive, long-running checks run once a day, at night. They run only for
repositories that changed since their last successful nightly. They do not
run on every pull request, and they do not run for every repository every
night.

## How a night runs

| Step | Owner | What happens |
|---|---|---|
| 07:17 UTC plan | Core [`org-nightly-dispatch.yml`](../.github/workflows/org-nightly-dispatch.yml) | Reads Core's registry projection and the admitted entry points, writes a plan. It reads no network and holds no credential. |
| Apply | Core [`tools/nightly_apply.py`](../tools/nightly_apply.py) | **Closed by default.** When opened, it dispatches each ELIGIBLE entry point through `workflow_dispatch`. |
| Deep run | Consumer entry point | Admits the dispatch, then runs the heavy suite once. |
| Core itself | Core [`nightly-deep.yml`](../.github/workflows/nightly-deep.yml) | Self-scheduled at 07:17. Skips, and records why, when main's HEAD already has a successful scheduled run. |

A repository is ELIGIBLE only when all of these hold:

- it is a current registry member in Core's projection;
- it has an admitted entry point;
- its default-branch head differs from its last successful `apply` run under
  the admitted contract;
- its permission and run history read cleanly;
- the dispatch key has not been seen in the entry point's history.

An unchanged repository is SKIPPED_ALREADY_TESTED or SKIPPED_INACTIVE. A
repeat run on the same day is SKIPPED_DUPLICATE. A permission or history read
that fails is UNKNOWN_*, dispatches nothing and fails the apply run.

## What each consumer must expose

One workflow file, named in Core's admission entry. The recommended name is
`.github/workflows/deep-plan.yml`, which is the shape Cognitive.Engine.Graphs
already has.

| Requirement | Value |
|---|---|
| Trigger | `workflow_dispatch` only. No `schedule`, `push` or `pull_request`. |
| Inputs, all `type: string`, in this order | `expected_sha`, `utc_slot`, `deep_contract`, `dispatch_key`, `mode` |
| `run-name` (required) | `deep ${{ inputs.mode }} ${{ inputs.utc_slot }} ${{ inputs.deep_contract }} ${{ inputs.dispatch_key }}` |
| Admission before any heavy step | Refuse unless the event is `workflow_dispatch`, the ref is the default branch, `expected_sha` is 40 lowercase hex and equals `github.sha`, `utc_slot` is a calendar date, `deep_contract` is the admitted contract, and `dispatch_key` matches `^[A-Za-z0-9._:-]{8,200}$`. |
| `mode` | `plan` runs no heavy job; `apply` runs the deep suite once. |
| Concurrency | One group per slot and contract, with `cancel-in-progress: false`. |
| Permissions | `contents: read`, plus only what the suite needs. |
| Receipt | A run that concludes `success` with `mode=apply` is the successful nightly for its `head_sha`. |

The `run-name` is load-bearing. Core reads `display_title` to find the last
successful `apply` and the keys already used. Without it, history is unknown
and the repository is never dispatched.

Core sends `dispatch_key` as `nd1-` followed by 40 hex characters. That is a
digest of organization, repository id, slot, entry point and contract. It
fits the bounded-token check that Cognitive.Engine.Graphs and
Enrichment.Inference.Engine already enforce.

The deep entry point may also host the scans that consumers schedule today
(CodeQL, SBOM and licence scans, Scorecard). It runs them as extra jobs
behind the same admission, so they too run only on a changed night.

## Per-repository status and the schedules the entry point replaces

Inventory read on 2026-11-20 at each default-branch head:

| Repository | Head |
|---|---|
| Cursor-Governance | `7921359` |
| Cognitive.Engine.Graphs (CEG) | `e1ccd1b` |
| Enrichment.Inference.Engine (EIE) | `4324e0d` |
| `.github` | `e872212` |
| IB-Odoo_19 | `Staging@341e45f` |

"Replaces" means the schedule becomes redundant once the entry point is
admitted, apply is open and a deep run has been observed. **No schedule is
retired before CURSOR_CANARY_PASS.**

### Quantum-L9/l9-ci-core (admitted, `scheduling: self`, `core-deep/v1`)

- Entry point: `nightly-deep.yml`. It is now change-gated by its receipt.
- It replaces nothing else. Core has no other heavy schedule.

### Quantum-L9/Cognitive.Engine.Graphs (admitted, `ceg-deep/v1`)

- Entry point `.github/workflows/deep-plan.yml` already conforms: it is
  dispatch-only, has `run-name`, and checks `ceg-deep/v1` and the bounded key.
- Replaces:
  - `codeql.yml`, weekly Mon 06:00. It has no `workflow_dispatch` today, so
    its analysis must move into the deep entry point or gain dispatch.
  - `supply-chain.yml` schedule, weekly Mon 03:00: Scorecard, licences and
    SBOM. `dependency-review` stays on pull requests.
  - `docs-code-sync.yml` schedule, weekly Mon 00:00. Its PR trigger stays.
- Projected: Core's projection was regenerated from `.github@08b2d07`, so
  CEG is a member, and the plan records it as SKIPPED_APPLY_CLOSED.

### Quantum-L9/Enrichment.Inference.Engine (admitted, `eie-deep/v1`)

- Entry point `.github/workflows/ci.yml` (`deep-admit` → `integration`).
  It does not conform yet:
  - no `run-name`, so history reads as unknown and it is never dispatched;
  - the deep path shares `concurrency: ci-${{ github.ref }}` with
    `cancel-in-progress: true`, so a push to main cancels a running deep job;
  - its inputs are optional and `mode` is free text.

  Moving the deep path into its own `deep-plan.yml` resolves all three.
- Replaces:
  - `codeql.yml`, weekly Mon 04:00. It has no `workflow_dispatch` today.
  - `supply-chain.yml` schedule, weekly Mon 03:00: licences, SBOM and the
    GPL/AGPL scan. `dependency-review` stays on pull requests.
- Projected, as CEG.

### Quantum-L9/Cursor-Governance (admitted 2026-11-20, `cursor-governance-deep/v1`)

- Entry point `.github/workflows/sonarcloud.yml`: dispatch-only, the five
  string inputs, the SHA and key admission before pytest, and a refusal of a
  repeated dispatch key.
- It does not conform yet on one row: its `run-name` is
  `SonarCloud <mode> <dispatch_key> <expected_sha>`, not
  `deep <mode> <utc_slot> <deep_contract> <dispatch_key>`. Core skips a title
  it cannot parse, so it sees no Cursor-Governance history: every night reads
  as changed and is dispatched, and Core cannot deduplicate a key (the
  repository's own guard refuses the repeat). Setting the contract
  `run-name` restores change gating; that is a Cursor-Governance change.
- Would replace, once apply has run it:
  - `codeql-nightly.yml`, daily 03:24, CodeQL security-extended;
  - `supply-chain.yml` schedule, weekly Mon 03:00, licences and SBOM.
- Not replaced (housekeeping, not a change-gated suite):
  - `branch-hygiene.yml`, weekly Mon 13:00;
  - `l9-canonical-identity-projection.yml`, every 6 h. It is a projection
    refresh against `.github`.

### Quantum-L9/l9-ci-debt-intelligence, l9-ci-debt-lsp, l9-ci-debt-resolver (admitted 2026-11-24, `dispatch`)

- Entry point `.github/workflows/deep-plan.yml` in each repository: dispatch
  only, the five string inputs in contract order, the contract `run-name`, SHA
  and key admission before any heavy step, `mode=plan` running nothing,
  `mode=apply` running `make setup` then `make test` under the v2 facade.
- Contracts: `intelligence-deep/v1`, `lsp-deep/v1`, `resolver-deep/v1`.
- Replaces the former `l9-nightly.yml` call to `nightly.yml@0d3d8d3`. That pin is
  dead: the called workflow was invalid for every caller (its `analyze` job
  granted `checks: write` above its own workflow-level grant) and has since been
  removed from Core main, where `nightly-deep.yml` is Core's own self-test and
  carries no `workflow_call` trigger.
- Semgrep analysis is not part of these entry points. It stays in
  `l9-analysis.yml`, which runs on pull requests and main; mixing SARIF analysis
  into the nightly is what forced `checks: write` and `security-events: write`
  into the nightly caller in the first place.

### Quantum-L9/.github (no entry point, none needed)

Every schedule is an organization-wide, time-based governance check. None
runs tests or a change-gated scan, so none is replaced:

| Workflow | Schedule |
|---|---|
| `audit-pins-org.yml` | monthly, 15th 12:00 |
| `enforce-policies.yml` | weekly, Wed 13:00 |
| `governance-report.yml` | weekly, Mon 13:23 |
| `preflight-scheduled.yml` | monthly, 1st 10:00 |
| `registry-completeness.yml` | daily, 05:41 |
| `secret-drift.yml` | weekly, Tue 06:17 |
| `stale.yml` | weekly, Mon 08:17 |
| `sync-labels-all.yml` | weekly, Mon 09:30 |

### cryptoxdog/IB-Odoo_19 (outside the organization)

The planner skips it as `outside_organization`. Its own
`nightly-full-python.yml` (daily 07:17, `Staging` only) is unaffected.

## Opening apply (operator decision, taken 2026-11-20)

The operator opened apply on 2026-11-20 (relayed by the CTO, App confirmed):

1. The credential is the `l9-nightly-dispatch` GitHub App (installed on the
   organization; actions read/write, contents read, metadata read). Its ID is
   repository variable `L9_NIGHTLY_APP_ID` and its key repository secret
   `L9_NIGHTLY_APP_PRIVATE_KEY` in `Quantum-L9/l9-ci-core`. No PAT is used.
2. `org-nightly-dispatch.yml` job `apply` (on the schedule, or a dispatch with
   `mode: apply`) mints an installation token scoped to the admitted
   `dispatch` repositories alone with `actions: write` and `contents: read`,
   and runs `python3 tools/nightly_apply.py apply` with
   `L9_NIGHTLY_APPLY=open` and the token in `L9_NIGHTLY_DISPATCH_TOKEN`.
   `tests/workflows/test_nightly_apply.py` fails if any other workflow does
   either, or if the token is not App-minted.
3. Keep Core's registry projection current
   (`tools/project_required_repositories.py --ref main --check`), so every
   admitted consumer is a member.

The test changes this decision required, and no more:
`tests/workflows/test_nightly_apply.py` moved from "no workflow opens apply"
to "exactly `org-nightly-dispatch.yml` opens apply, with an App-minted token,
and its only secret is the App key"; `tests/workflows/test_org_nightly_dispatch.py`
expects the `apply` mode and keeps the plan job credential-free.

Apply only dispatches. It never merges, never checks out a consumer and
never runs a suite itself.
