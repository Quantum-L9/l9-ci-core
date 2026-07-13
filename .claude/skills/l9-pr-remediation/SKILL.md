---
name: l9-pr-remediation
description: closed-loop pull request remediation and review resolution across one or more prs — ingest ci gate failures and code-review comments (coderabbit, gemini, copilot, sonarcloud, human), validate every suggestion against the current code and reject false positives, apply batched fixes as one commit per cycle, verify all ci gates locally before push, reply to and resolve every thread, then loop until ci is green and no new actionable comments remain. use when a pr has failing ci, unresolved reviewer or bot comments, or when the user asks to fix a pr, remediate review feedback, resolve review comments, babysit or converge a pr, or run a pr improvement loop.
skill_schema: 1
layer: control_plane
role: skill_entrypoint
tags: [l9, pr, ci, code-review, recursive, remediation, review-resolver, github, convergence, exemplary]
owner: igor_beylin
status: active
version: 3.2.0
updated: 2026-07-13
sources:
  - l9-pr-remediation@2.1.0 (skill)
  - pr-review-resolver@1.x (agent)
  - L9 Compounding Leverage Kernel@1.0.0
  - Frictionless Leverage / Minimize-Friction analysis prompt
---

# PR Remediation & Review Resolution Loop

## Purpose

Operate a closed-loop remediation cycle over one or more open pull requests. Ingest CI gate failures AND code-review comments (Gemini, CodeRabbit, GitHub Copilot, SonarCloud, human reviewers), **validate every suggestion against the current code before touching anything** (review bots have a high false-positive rate), apply the accepted fixes in ONE batched commit per PR per cycle, verify ALL CI gates locally before any push, reply to and resolve every review thread with canonical responses, then loop until CI is green and no new actionable comments remain.

This skill consolidates two predecessors into one control plane: the **CI convergence loop** (local-verify-first, one-push-per-cycle, poll-until-green) and the **review-comment resolver** (scope discovery across PRs, confidence-gated validation, per-suggestion classification, machine-readable run report). There is no separate agent — the resolver's judgment lives here as `references/finding-classifier.md` and `references/review-replies.md`.

## Core Contract

| Input | Source | Tool |
|-------|--------|------|
| PR scope | `ALL_OPEN` \| `PR_NUMBERS` \| `LABEL:<x>` \| `AUTHOR:<u>` | `gh pr list` |
| CI failures | GitHub Actions logs | `gh run view --log-failed` |
| Review comments | PR review threads | `gh api …/pulls/{pr}/reviews` + `gh pr view --comments` |
| Inline suggestions | PR diff comments | `gh api …/pulls/{pr}/comments` |
| Thread resolution state | GraphQL `reviewThreads` | `gh api graphql` |
| CI gate definitions | `.github/workflows/*.yml` + `package.json` | File read (gate discovery) |

**Tooling portability — capability map.** Commands are shown with the `gh` CLI for concreteness, but the *operation* is the contract, not the CLI. In an environment without `gh` (e.g. GitHub MCP tools, or the REST/GraphQL API directly), select the row by operation — do not treat a missing `gh` binary as a blocker. Exact GitHub MCP tool names vary by server; match on the operation, not the literal name.

| Operation | `gh` CLI | GitHub MCP tool (typical) | REST / GraphQL |
|-----------|----------|---------------------------|----------------|
| List PRs in scope | `gh pr list` | `list_pull_requests` | `GET /repos/{o}/{r}/pulls` |
| Get run status | `gh run list --branch` | `actions_list` | `GET /repos/{o}/{r}/actions/runs` |
| Get failed-run logs | `gh run view --log-failed` | `get_job_logs` | `GET …/actions/runs/{id}/logs` |
| List reviews | `gh api …/pulls/{n}/reviews` | `pull_request_read` (reviews) | `GET …/pulls/{n}/reviews` |
| List inline comments | `gh api …/pulls/{n}/comments` | `pull_request_read` (comments) | `GET …/pulls/{n}/comments` |
| Reply to inline comment | `gh api …/comments/{id}/replies` | `add_reply_to_pull_request_comment` | `POST …/pulls/{n}/comments/{id}/replies` |
| PR-level comment | `gh pr comment` | `add_issue_comment` | `POST …/issues/{n}/comments` |
| Resolve a thread | `gh api graphql` (mutation) | `resolve_review_thread` | GraphQL `resolveReviewThread` |
| Create deferred issue | `gh issue create` | `issue_write` | `POST /repos/{o}/{r}/issues` |

**Single ingress:** `SKILL.md` is the single control plane. Scope is normalized and validated once (Step 1), then routed to the per-PR loop; per-PR state is tracked independently and never shared across PRs. No module is entered except through this workflow.

| Output | Condition |
|--------|-----------|
| ONE commit pushed per PR | Every cycle that produces actionable changes |
| Canonical reply on EVERY thread | Every cycle, after push |
| Threads resolved; deferred issues created | Every cycle |
| Batch summary comment | Every cycle, after replies |
| Machine-readable run report (JSON) | Final cycle, per PR |
| Convergence report | Final cycle |

## Authority Order

1. Latest user instruction (PR scope, repo, specific directions, config overrides).
2. CI failure logs (exact error output from the failing gate — the merge blocker).
3. Repo ground truth: `.github/workflows/*.yml`, `tsconfig.json`, `package.json`, lint/type configs.
4. Review comments — precedence when they conflict: **human > blocking > higher-confidence > more recent**.
5. This skill's references.
6. `Unknown` — do not invent fixes for unclear comments; defer and ask.

## Non-Negotiable Rules

1. **Validate before you act.** Read the actual current file — never trust the comment snippet. Reject or defer suggestions that reference changed/absent code, would break a passing gate, contradict a project convention, or fall below the confidence gate. Reject explicitly with a reason; never silently skip.
2. **ONE commit, ONE push per PR per cycle.** All fixes for a cycle are batched into a single commit + single push. Multiple pushes per cycle is a protocol violation.
3. **Local verify is a BLOCKING GATE.** Run ALL CI gate commands locally and confirm exit 0 before any push. If local verify fails, fix it BEFORE pushing — never push and hope CI catches it.
4. **Gate discovery BEFORE fixing.** Parse ALL workflow YAML to enumerate every CI gate command before applying any fix. No surprises from unknown gates.
5. **Remote CI is confirmation, not discovery.** After push, CI polling confirms what local verify already proved. A remote failure local verify missed is a documented protocol delta.
6. **Every thread gets a reply and is resolved.** No silent fixes. Deferred items get a linked issue.
7. **One logical fix = one focused change.** Never bundle unrelated changes; keep the diff to the referenced file/lines unless the fix legitimately requires related code.
8. **Never force-push, never rewrite shared history, never touch a PR outside scope.**
9. **Idempotent.** Re-running must not duplicate commits or re-apply already-resolved threads. The idempotency key is the triple **(resolved thread state, reply marker `<!-- l9-remediation:{pr}:{finding_id} -->`, commit trailer `Remediation-Cycle: {repo}#{pr}/cycle-{N}`)** — checked during ingestion (`references/signal-ingestion.md` §Idempotency).
10. **Preserve attribution:** reference the review thread/commit in each fix.
11. **MUST NOT loop more than `max_cycles`** (default 3). When CI is the task ("get it green"), re-diagnose and re-kick each cycle rather than giving up after one.
12. **MUST NOT fix "discussion"/"question" comments** without user confirmation.
13. **When review comments conflict with CI**, CI wins (it blocks merge).
14. **Enforcement gates are mandatory** — each step produces a required artifact (see `references/enforcement-gates.md`). Cannot advance without it.
15. **Never expose secrets** in commits, logs, or replies.

## Compact Workflow

Each step produces a required gate artifact (`references/enforcement-gates.md`). Do NOT advance a gate without its artifact.

1. **Resolve scope** — expand PR scope to a concrete PR list (`references/signal-ingestion.md` §Scope). For each PR, run steps 2–10.
2. **Discover CI gates** — read ALL `.github/workflows/*.yml` + `package.json` scripts. Build the local verify command list. → **Gate A.**
3. **Ingest signals** — `references/signal-ingestion.md`. Fetch CI status + failed logs; all unresolved review threads + inline suggestions; normalize to one finding list.
4. **Classify & validate** — `references/finding-classifier.md`. Route CI failures by type; classify review comments `AUTO_APPLY | VALIDATE | DEFER | IGNORE`; validate each against current code; score confidence; reject false positives with reasons. → **Gate B.**
5. **Apply ALL accepted fixes** — `references/fix-engine.md`. Fix blocking CI items + accepted review items; skip discussion/deferred; do NOT commit yet. → **Gate C** (`git diff --stat`).
6. **Local verify (BLOCKING GATE)** — run EVERY CI gate command locally; on any failure, fix and re-run ALL gates (max 5 iterations); proceed only when fully green. → **Gate D** (all exit codes 0).
7. **Commit & push (ONCE)** — single conventional commit, single push to the PR head branch (or, when `branch_policy: follow_up_pr`, open one follow-up PR targeting the original branch). → **Gate E** (commit SHA, push count = 1).
8. **Reply & resolve** — `references/review-replies.md`. Canonical reply on every thread (Fixed/Deferred/Acknowledged/Disagreed), create deferred issues, resolve all threads, post batch summary. → **Gate F.**
9. **Wait & confirm** — `references/convergence-loop.md`. Poll CI to completion; check for new comments after push; if new actionable signals → loop to step 3; if CI green AND none → converge.
10. **Report** — emit the machine-readable run report (per PR) + convergence block. → **Gate G.**

## Resource Map

- [references/signal-ingestion.md](references/signal-ingestion.md) — PR scope discovery; fetch + parse CI logs, review threads, inline suggestions, workflow YAML; unified finding format; bot detection; dedup.
- [references/finding-classifier.md](references/finding-classifier.md) — severity + fix-strategy routing; **validation doctrine** (reject false positives), confidence gate, `AUTO_APPLY/VALIDATE/DEFER/IGNORE`, conflict precedence, batch plan.
- [references/fix-engine.md](references/fix-engine.md) — fix methodology per finding type, minimal-diff discipline, batch rules, local-verify BLOCKING protocol, rollback.
- [references/review-replies.md](references/review-replies.md) — canonical reply formats, thread resolution, deferred issues, batch summary, **machine-readable run report**, downstream leverage.
- [references/convergence-loop.md](references/convergence-loop.md) — wait/poll/re-check, convergence gate, cycle tracking, stop conditions, configuration.
- [references/enforcement-gates.md](references/enforcement-gates.md) — **runtime enforcement layer**: required proof-of-compliance artifact at each step; protocol-violation detection.
- [schemas/run-report.schema.json](schemas/run-report.schema.json) — **canonical machine artifact**: the single normative shape for the gate artifacts, the per-PR run report, the convergence block, and the drift signals. The reference-doc snippets are non-normative views of this schema.
- [scripts/validate_run_report.py](scripts/validate_run_report.py) — deterministic run validator: checks an emitted run report against the schema + cross-field hard invariants. `python3 scripts/validate_run_report.py <run-report.json>`.

## Exemplary Provenance & Self-Improvement

This pack was compiled through the L9 exemplary pipeline: `parse_source → extract_expertise → compress_expertise → design_skill → run_exemplary_gate → package`. The compressed intelligence layer is shipped as auditable artifacts, not prose:

- [expertise_model.yaml](expertise_model.yaml) — experts, doctrine, invariants, authority hierarchy, activation/reject signals, adapters, failure modes, leverage points (the `extract_expertise` / `compress_expertise` output).
- [skill_intelligence_report.yaml](skill_intelligence_report.yaml) — activation model (with measured specificity + false-positive-risk scores), authority model, expert heuristics, adapter map, evidence hierarchy, `exemplary_gate` results, and the tier decision.
- Deterministic gate: [scripts/validate_exemplary_skill.py](scripts/validate_exemplary_skill.py) — run `python3 scripts/validate_exemplary_skill.py .` from the pack root; `tier: exemplary` is claimed only because this validator passes.

**Run validation (machine-enforced rails):** a remediation run is not trustworthy because the doctrine says so — it is trustworthy because [`scripts/validate_run_report.py`](scripts/validate_run_report.py) accepts the emitted run report against [`schemas/run-report.schema.json`](schemas/run-report.schema.json). The validator enforces the cross-field invariants the gates promise (`push_count_this_cycle == 1`, `local_verify_log.all_green`, `gates_run == gate_registry.total_gates`, `threads_replied == threads_total`, every rejection/deferral carries a reason). It degrades gracefully: full JSON-Schema validation when `jsonschema` is installed, structural + invariant checks otherwise.

**Leverage & friction filters applied** (per the two L9 leverage kernels): every reference must accelerate future PR cycles and emit a reusable primitive — the gate registry, the finding taxonomy, the canonical reply templates, and the machine-readable run/convergence report schemas are those primitives. Friction removed: deterministic gate discovery (no blind fixing), local-verify-before-push (no CI ping-pong), one-commit-per-cycle (clean history), explicit reject reasons (bot false-positive suppression compounds over time).

**After-use improvement hook** — capture ONLY when the user reports a bad run or asks to iterate: `missed_trigger`, `false_trigger` (bad suggestion applied), `recurring_user_correction`, or `output_that_required_manual_rework`. Feed captures back into `finding-classifier.md` (confidence gate) and `activation_signals`.

## Configuration

Defaults (overridable by the user):

```yaml
pr_scope: ALL_OPEN            # ALL_OPEN | PR_NUMBERS:[...] | LABEL:<x> | AUTHOR:<u>
max_cycles: 3
confidence_gate: 0.75         # min confidence to auto-apply a review suggestion
poll_interval_seconds: 45
max_wait_per_cycle_minutes: 10
max_local_verify_iterations: 5
branch_policy: push_to_head   # push_to_head | follow_up_pr
review_agents: [gemini-code-assist, coderabbitai, copilot, sonarcloud]
auto_fix_nits: false
skip_bot_discussions: true
parallel_triage_threshold: 3
```

## Validation

Before declaring convergence (per PR):
- CI status is `success` on the latest commit.
- No new unresolved review comments after the last push.
- Every thread replied to and resolved; every deferred item has a linked issue.
- Every accepted finding addressed; every rejected/deferred finding has an explicit reason.
- All enforcement-gate artifacts produced for the final cycle.
- Machine-readable run report + convergence block emitted.

## Failure Handling

- CI logs unavailable → STOP; ask for run ID or pasted logs.
- Review API rate-limited → wait 60s, retry once, then STOP.
- Branch protected without push rights, or PR has merge conflicts → HALT and report (do not guess).
- Fix causes a new CI failure → revert that fix, mark the finding deferred ("fix causes regression"), continue.
- Local verify passes but remote CI fails → investigate environment delta, document, defer if unresolvable.
- Two suggestions conflict on the same lines → apply precedence (human > blocking > higher-confidence > more recent); if still unclear, defer and ask.
- Max cycles reached without convergence → emit `partial` with remaining items.
- Gate artifact cannot be produced → STOP at that gate, report `blocked`.
