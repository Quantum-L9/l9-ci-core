<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: review_replies
tags: [pr, review, replies, threads, resolution, run-report, leverage]
owner: igor_beylin
status: active
version: 3.3.0
updated: 2026-07-13
/L9_META -->

# Review Reply Protocol & Run Report

## Purpose

After pushing fixes, reply to every review thread with a canonical response, resolve the thread, create trackable artifacts for deferred items, post a batch summary, and emit a machine-readable run report so downstream automation can route on PR state.

## Non-Negotiable Rules

1. **Every thread gets a reply.** No silent fixes.
2. **Replies follow a canonical format** — structured, not freeform.
3. **Resolve threads after replying.**
4. **Deferred items get linked issues** — never defer without a trackable artifact.
5. **Batch summary posted as the final PR comment.**
6. **Every reply carries an idempotency marker** (see below) so re-runs never double-post.

## Idempotency Marker

Append a hidden HTML-comment marker to every canonical reply:

```markdown
<!-- l9-remediation:{pr}:{finding_id} -->
```

**Before posting a reply, scan the thread for this exact marker.** If it is already present, the finding was replied to on a prior cycle — skip it (do not re-post, do not re-resolve). Combined with thread-resolved state and the commit `Remediation-Cycle:` trailer (`fix-engine.md`), this makes a re-run produce **zero duplicate artifacts**. The marker is invisible in rendered GitHub markdown.

## Canonical Reply Formats

### Format A: Fixed
```markdown
**Fixed** in `{sha_short}`

{one-line description of the change}

`{file}:{line}` — {before → after}
```
Then **resolve the thread**.

### Format B: Deferred
```markdown
**Deferred** → #{issue_number}

Reason: {why not fixable this cycle}
Scope: {what would need to change}
Proposed resolution: {suggested approach}
```
**Create the issue first**, reply with the link, then **resolve**.

### Format C: Acknowledged (discussion)
```markdown
**Acknowledged** — not actioned this cycle

{brief response}

Tracking: {where captured, if anywhere}
```
Then **resolve**.

### Format D: Disagreed (false positive / intentional)
```markdown
**Disagree** — {reason category}

{why the suggestion does not apply}

Evidence: {docs / type definition / code that proves the point}
```
Reason categories: `false positive` · `intentional design` · `conflicts with {X}` · `already handled`. Then **resolve**.

Every rejected/ignored finding from the classifier MUST surface as a Format C or D reply — the reason is never dropped.

## How to Post

```bash
# Reply to an inline (diff) comment
gh api /repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies -f body="{reply}"
# Reply at PR level
gh pr comment {pr} --repo {owner}/{repo} --body "{reply}"
# Resolve a thread (use thread_id captured during ingestion)
gh api graphql -f query='
  mutation($t:ID!){ resolveReviewThread(input:{threadId:$t}){ thread{ isResolved } } }' -f t="{thread_id}"
# Create a deferred issue
gh issue create --repo {owner}/{repo} \
  --title "Deferred from PR #{pr}: {short}" \
  --body "{context + proposed resolution}" --label "deferred,from-review"
```

## Batch Summary Comment

```markdown
## PR Remediation — Cycle {N} Summary

**Commit:** `{sha}` | **Findings:** {total} | **CI gates:** {count} passed locally

### Fixed ({n})
| Finding | File | Change |
|---------|------|--------|

### Deferred ({n})
| Finding | Reason | Issue |
|---------|--------|-------|

### Acknowledged / Disagreed ({n})
| Finding | Reason |
|---------|--------|

---
*Local verify: {N} gates, all exit 0 | Threads resolved: {count}/{total}*
```

## Machine-Readable Run Report (per PR — Gate G input)

Emit once per PR at convergence so other agents/dashboards can consume state without re-parsing GitHub. **Normative shape:** [`schemas/run-report.schema.json`](../schemas/run-report.schema.json) — the example below is a human view; the schema is the source of truth, and the emitted file MUST pass [`scripts/validate_run_report.py`](../scripts/validate_run_report.py). The report also carries the drift signals (`summary.bot_false_positive_rate`, `convergence.cycles_exhausted`).

```json
{
  "schema_version": "1.0",
  "run": { "run_id": "<timestamp>", "repo": "OWNER/REPO",
           "pr": { "number": 0, "branch": "", "cycles_run": 0 } },
  "gates": { "gate_registry": {}, "classified_findings": {}, "local_verify_log": {},
             "push_record": {}, "reply_record": {}, "report_record": {"run_report_emitted": true} },
  "findings": {
    "applied": [ {"reviewer": "", "file": "", "lines": "", "commit": "<sha>",
                  "confidence": 0.0, "disposition": "AUTO_APPLY", "tests": "pass"} ],
    "deferred": [ {"reviewer": "", "file": "", "reason": "", "issue": 0} ],
    "rejected": [ {"reviewer": "", "file": "", "reason": ""} ]
  },
  "convergence": { "convergence_status": "converged", "pushes_total": 0, "commits_pushed": ["<sha>"],
                   "cycles_exhausted": false, "protocol_violations": [], "minimum_safe_next_action": "merge" },
  "summary": { "fixes_applied": 0, "deferred": 0, "rejected": 0,
               "bot_false_positive_rate": {"coderabbitai": 0.0} }
}
```

`summary.bot_false_positive_rate[reviewer] = rejected / (applied + rejected)` for that reviewer over the run. It is a drift signal consumed by the confidence-gate self-tuning rule in `finding-classifier.md`.

## Downstream Leverage

| Reply | Leverage |
|-------|----------|
| Fixed | searchable commit↔comment link; future grep finds the decision |
| Deferred | backlog item with full context; prioritizable |
| Acknowledged | knowledge captured; bot training signal |
| Disagreed | reduces future false positives; documents intentional design |
| Run report | machine-consumable state for routing/dashboards |

## Ordering

1. Reply to all **Fixed** threads. 2. Create issues for **Deferred**, then reply with links. 3. **Acknowledged**. 4. **Disagreed**. 5. Batch summary. 6. Resolve all threads. 7. Emit run report.

## Validation (Gate F)

- [ ] Every unresolved thread has a reply
- [ ] Every thread resolved
- [ ] Every deferred item has a linked issue
- [ ] Batch summary posted
- [ ] Run report emitted
- [ ] reply count == thread count; every rejection carries a reason
