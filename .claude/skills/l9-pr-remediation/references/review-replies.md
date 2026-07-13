<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: review_replies
tags: [pr, review, replies, threads, resolution, run-report, leverage]
owner: igor_beylin
status: active
version: 3.0.0
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

Emit once per PR at convergence so other agents/dashboards can consume state without re-parsing GitHub:

```json
{
  "run_id": "<timestamp>",
  "repo": "OWNER/REPO",
  "pr": {
    "number": 0,
    "branch": "",
    "cycles_run": 0,
    "threads_total": 0,
    "applied": [
      {"reviewer": "", "file": "", "lines": "", "commit": "<sha>",
       "confidence": 0.0, "disposition": "AUTO_APPLY|VALIDATE", "tests": "pass"}
    ],
    "deferred": [{"reviewer": "", "file": "", "reason": "", "issue": 0}],
    "rejected": [{"reviewer": "", "file": "", "reason": ""}],
    "commits_pushed": ["<sha>"],
    "ci_status": "success|failure|skipped"
  },
  "summary": {"fixes_applied": 0, "deferred": 0, "rejected": 0}
}
```

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
