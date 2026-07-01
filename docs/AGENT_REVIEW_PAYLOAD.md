<!-- L9_META
l9_schema: 1
origin: l9-ci-universal-base
layer: [docs, ci, agent-payload]
tags: [L9_TEMPLATE, agent-payload, matrix-safe]
owner: platform
status: active
/L9_META -->

# Agent Review Payload

`agent_review_payload.json` is the machine-readable CI state consumed by future Audit, Implementer, and Validator loops.

CI comments remain human-facing. Agents should consume JSON artifacts, not scrape markdown.

## Matrix-safe flow

Each matrix or stage job emits a unique summary:

```bash
l9-ci run-pipeline --stage test --matrix python=3.12 --emit-dir artifacts/ci
```

The final aggregation step consumes every `*_ci_summary.json` file:

```bash
l9-ci render-agent-payload --input-dir artifacts/ci --output artifacts/agent_review_payload.json
```

## Safety split

The payload separates:

- `autofix_candidates`: deterministic mechanical fixes only.
- `manual_review_required`: logic, security, routing, and uncertain findings.

Agents must not treat `manual_review_required` as safe to patch automatically.
