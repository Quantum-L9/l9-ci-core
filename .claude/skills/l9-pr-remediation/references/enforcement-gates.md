<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: enforcement_gates
tags: [pr, validation, enforcement, checkpoints, artifacts, protocol-violation]
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-07-13
/L9_META -->

# Enforcement Gates (Runtime Layer)

## Purpose

Prevent protocol violations by requiring a concrete artifact at each workflow step. Rules without enforcement are suggestions. Each gate below defines the **proof-of-compliance** artifact the agent MUST produce before advancing. If the artifact is missing or invalid, do NOT proceed.

## Gate Map

```text
Step 1 [GATE A] Step 2-4 [GATE B] Step 5 [GATE C] Step 6 [GATE D] Step 7 [GATE E] Step 8 [GATE F] Step 9-10 [GATE G]
```

## Gate A: Scope + Gate Discovery Complete (Steps 1–2)

```yaml
gate_registry:
  pr_scope_total: {int >= 1}
  total_gates: {int >= 1}
  gates:
    - { name: "{gate}", command: "{exact command}", source: "{workflow}:{step}", can_run_locally: true|false, requires_secrets: true|false }
```
Validation: `pr_scope_total >= 1`; `total_gates >= 1`; every gate has a non-empty `command` traced to a source.
**STOP if:** no PRs in scope → report and stop. No workflow/scripts found → ask if CI is configured.

## Gate B: Ingestion + Classification + Validation Complete (Steps 3–4)

```yaml
classified_findings:
  blocking: {count}
  auto_apply: {count}
  validate: {count}
  discussion: {count}
  deferred: {count}
  ignored: {count}       # false positives, each with a rejection reason
  total: {int}
execution_plan:
  cycle_scope: ["{id}", ...]
  estimated_files: ["{path}", ...]
  local_verify_commands: ["{command}", ...]   # must equal the gate registry
```
Validation: `cycle_scope` non-empty (else skip to convergence); `local_verify_commands == gate_registry`; every finding has `id/source/severity/message`; every `ignored`/`deferred` has a reason.
**STOP if:** total findings == 0 AND CI green → already converged, emit report.

## Gate C: All Fixes Applied — Pre-Verify (Step 5)

```bash
git diff --stat
```
Validation: diff non-empty; files changed ≤ `estimated_files` + 2; no unrelated files; every `cycle_scope` finding addressed.
**STOP if:** diff empty → no fixes applied; re-read findings.

## Gate D: Local Verify Passed — CRITICAL (Step 6)

```yaml
local_verify_log:
  iteration: {1-5}
  gates_run: {int}
  gates_passed: {int}
  all_green: true
  results:
    - { gate: "{name}", command: "{cmd}", exit_code: 0 }
```
Validation: `all_green: true`; `gates_run == gate_registry.total_gates`; `iteration <= 5`; every registry gate present in results.
**This is the ONLY gate that authorizes a push. Pushing without `all_green: true` is a protocol violation.**
**STOP if:** not green after iteration 5 → defer problematic findings, re-verify the rest.

## Gate E: Single Commit, Single Push (Step 7)

```yaml
push_record:
  commit_sha: "{40-char hex}"
  commit_message: "fix(pr-remediation): cycle {N} — ..."
  files_in_commit: {int}
  push_count_this_cycle: 1
  branch: "{branch}"
```
Validation: `push_count_this_cycle == 1`; valid SHA; conventional message; `git log --oneline HEAD~1..HEAD` == 1 line; no force-push.
**STOP if:** push failed → check auth/remote/branch protection; ask user if needed.

## Gate F: Review Replies Complete (Step 8)

```yaml
reply_record:
  threads_total: {int}
  threads_replied: {int}
  threads_resolved: {int}
  issues_created: {int}
  batch_summary_posted: true
  reply_breakdown: { fixed: {n}, deferred: {n}, acknowledged: {n}, disagreed: {n} }
```
Validation: `threads_replied == threads_total`; `threads_resolved == threads_total`; `issues_created >= deferred_count`; batch summary posted; every rejection carries a reason.
**STOP if:** API rate-limited → wait 60s, retry; if still failing, log partial and continue.

## Gate G: Report Emitted (Steps 9–10)

```yaml
report_record:
  run_report_emitted: true       # machine-readable JSON, per PR
  convergence_status: converged | partial | blocked
  cycles_run: {int}
  pushes_total: {int}            # should equal cycles_run
  unknowns: ["none" | ...]
```
Validation: run report emitted per PR; convergence block present with all fields; `pushes_total == cycles_run`.

## Protocol Violation Detection

- Push without Gate D `all_green: true` → **push-before-verify**
- More than one push per cycle → **multi-push**
- Skip Gate A (no gate registry) → **blind-fixing**
- Apply a suggestion without validating current code → **false-positive-applied**
- Leave threads unresolved after Step 8 → **silent-fix**

Violations MUST be logged in the convergence report under `protocol_violations`, reported to the user, and fed to the after-use improvement hook.

## Enforcement Mechanism

Produce each gate artifact in working notes/response before advancing. Artifacts are proof of compliance, a self-check that forces the work to actually happen, and a rollback anchor. If an artifact cannot be produced honestly, the agent is stuck at that gate and MUST fix the blocker, ask the user, or emit a `blocked` status — never fabricate an artifact.
