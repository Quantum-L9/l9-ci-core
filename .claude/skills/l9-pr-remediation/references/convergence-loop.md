<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: convergence_loop
tags: [pr, convergence, loop, polling, ci, local-verify, stop-conditions]
owner: igor_beylin
status: active
version: 3.1.0
updated: 2026-07-13
/L9_META -->

# Convergence Loop

## Purpose

After pushing fixes (already passed local verify), wait for CI to confirm, check for new reviews, then decide whether another cycle is needed or convergence is reached.

## Key Principle: Local-First Verification

Remote CI polling is a **CONFIRMATION** step, not a **DISCOVERY** step. All failures must be caught by local verify before push. If remote CI finds something local verify missed, that is a documented protocol delta, fed to the next cycle.

## Loop Architecture

```text
CYCLE:  ingest → classify+validate → fix ALL → local verify (GATE) → commit(ONE) → push(ONE)
                                   │
                                   ▼
CONVERGENCE CHECK:
  1. Wait for CI completion (confirmation)
  2. Check CI status
  3. Check for new review comments (created after last push)
  4. Evaluate convergence gate
     → converged: STOP + report
     → new actionable comments: loop (if cycles < max)
     → CI failed unexpectedly: investigate delta, fix, loop
     → max cycles: STOP + partial report
```

## Wait Protocol

```bash
gh run list --branch {branch} --limit 1 --json databaseId,status,conclusion
# or, when a single run id is known:
gh run watch {RUN_ID} --exit-status
```

Poll interval: 45s (local verify already passed — no urgency). Max wait: 10 min/cycle. If CI hasn't started after 2 min, check `gh workflow list --json name,state`.

### CI failure after local verify passed

1. `gh run view {RUN_ID} --log-failed`. 2. Identify the **delta**: missing secrets/env, different runtime version, missing system deps, network-dependent step, or parallel-job race. 3. Fixable locally → fix, re-verify, push (same cycle if within the commit). 4. Environment-only → add skip/`continue-on-error`, verify, push. 5. Unfixable → defer with reason "CI environment delta".

## Convergence Gate — reached when ALL true

| Condition | Check |
|-----------|-------|
| CI status `success` | `gh run view --json conclusion` |
| No new unresolved comments | compare counts/timestamps before/after push |
| All blocking findings resolved | internal tracking |
| All accepted findings resolved or deferred | internal tracking |

## Re-Ingestion (when not converged)

Re-ingest only NEW signals: failures on the latest run only; comments with `created_at` after the last push; drop findings whose thread was resolved. Do NOT re-process already fixed/deferred findings (idempotency).

## Cycle Tracking

```yaml
cycle_state:
  current_cycle: 1
  max_cycles: 3
  push_timestamps: ["2026-07-13T10:00:00Z"]
  findings_fixed: ["ci-1", "review-3"]
  findings_deferred: ["review-7"]
  findings_remaining: ["ci-2"]
  local_verify_gates_count: 6
  local_verify_passed_before_push: true
```

## Convergence Report

```yaml
convergence_status: converged | partial | blocked
cycles_run: {int}
pushes_total: {int}      # should equal cycles_run
commits_total: {int}     # should equal cycles_run
findings_summary: { total_ingested: {int}, fixed: {int}, deferred: {int}, remaining: {int} }
ci_gates_discovered: {int}
local_verify_iterations: {int}
local_verify_green_before_every_push: true | false
ci_status: success | failure
new_comments_after_final_push: {int}
deferred_items:
  - { id: "review-7", reason: "Requires architectural decision" }
protocol_violations: ["None"]
minimum_safe_next_action: "merge" | "manual review of deferred items" | "run another cycle"
```

## Stop Conditions

- `cycles_run >= max_cycles` → `partial`
- CI passes AND no new comments → `converged`
- Fix causes an unrecoverable regression → `blocked`
- GitHub API rate-limited and retry fails → `blocked`
- User sends a stop signal → `partial` with current state

## Configuration

Canonical defaults live in `SKILL.md` §Configuration (single source of truth — do not restate values here to avoid drift). Loop-relevant keys consumed by this reference: `max_cycles`, `poll_interval_seconds`, `max_wait_per_cycle_minutes`, `max_local_verify_iterations`. All are user-overridable.
