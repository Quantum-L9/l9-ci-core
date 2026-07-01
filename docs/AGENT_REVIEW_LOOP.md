<!-- L9_META
l9_schema: 1
origin: l9-ci-core
layer: [ci, docs, agent-review-loop]
tags: [L9_CI, agent-review-loop, gated-review, llm-router]
owner: platform
status: active
/L9_META -->

# L9 Agent Review Loop

Gated multi-agent code review for every L9 repo. Enables the CI Kernel's Agent
Review Loop (previously `prepared_not_enabled`): PR type + factors decide which
review agents run and which LLM model each uses (via `@quantum-l9/llm-router`).

## Doctrine posture

- **Advisory-only. No merge authority.** The PR Pipeline Gate remains the merge
  authority. The review loop never fails the build. A finding blocks only if its
  `rule_id` is promoted in `blocking-policy.yaml → review_blocking_promotions`
  (empty by default).
- **One idempotent comment** under `<!-- l9-agent-review-marker: v1 -->`
  (comment-protocol: find-by-marker → update → never duplicate; truncated).
- **No secret access beyond routing; raw `llm_response` is never persisted.**

## Pieces

| Where | What |
|---|---|
| `l9-ci-sdk :: l9_ci/review/` | agents (`audit_review_agent` deterministic, `llm_review_agent`), policy, router client, evals; `l9-ci review` / `l9-ci review-eval` |
| `.github/governance/review-routing-policy.yaml` | `(pr_class × factors) → agents + model_tier + mode` |
| `.github/governance/blocking-policy.yaml` | `review_blocking_promotions` (advisory→blocking gate) |
| `.github/governance/comment-protocol.yaml` | marker + truncation rules |
| `.github/workflows/code-review.yml` | reusable advisory review workflow |
| `.github/actions/llm-router/` | Node shim → `@quantum-l9/llm-router` (pending issue #4) |
| `schemas/agent-review-report.schema.json` | review report contract |

## How it runs

`pr-pipeline.yml` calls `code-review.yml` (deterministic-only, advisory). To
enable the LLM lane, call `code-review.yml` directly:

```yaml
jobs:
  agent_review:
    uses: Quantum-L9/l9-ci-core/.github/workflows/code-review.yml@v0.1.0
    with:
      pr-class: ${{ needs.classify.outputs.pr_class }}
      agents: "audit llm"
      review-mode: "advisory"
    secrets: inherit
```

The LLM lane self-guards: absent router/keys ⇒ Null client (no findings), so it
is safe to enable before issue #4 (LLM-Router install/auth) is resolved.

## Rollout

`shadow → advisory → selective blocking`. Start shadow/advisory; after the pilot,
promote specific rule_ids in `blocking-policy.yaml`. LLM prompt/agent changes
trigger `l9-ci review-eval` (golden sets + rubric) per Platform Doctrine.
