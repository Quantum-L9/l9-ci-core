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

### Quantum-L9/Cursor-Governance (not admitted)

- It has no deep entry point. Its test suite runs only on PR, push and
  dispatch (`l9-lint-test.yml`).
- Would replace, once it exposes one:
  - `codeql-nightly.yml`, daily 03:24, CodeQL security-extended;
  - `supply-chain.yml` schedule, weekly Mon 03:00, licences and SBOM.
- Not replaced (housekeeping, not a change-gated suite):
  - `branch-hygiene.yml`, weekly Mon 13:00;
  - `l9-canonical-identity-projection.yml`, every 6 h. It is a projection
    refresh against `.github`.

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

## Opening apply (operator decision)

Apply stays closed until an operator does all of these:

1. Provide a credential that can read contents and metadata and write
   `actions` on the admitted consumers, as an installation token. This
   repository holds no such credential and creates none.
2. Add a step that runs `python3 tools/nightly_apply.py apply` with
   `L9_NIGHTLY_APPLY=open` and the credential in
   `L9_NIGHTLY_DISPATCH_TOKEN`. No workflow sets either today, and
   `tests/workflows/test_nightly_apply.py` fails if one does.
3. Keep Core's registry projection current
   (`tools/project_required_repositories.py --ref main --check`), so every
   admitted consumer is a member.

Apply only dispatches. It never merges, never checks out a consumer and
never runs a suite itself.
