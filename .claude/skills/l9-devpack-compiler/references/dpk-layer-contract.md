<!-- L9_META
l9_schema: 1
parent: l9-devpack-compiler
layer: reference
role: dpk_layer_contract
tags: [dpk, layers, manifest, repository-map, constraints, task-contract, runbook, debt, templates]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-15
/L9_META -->

# DPK-1.0 Layer Contract & Templates

## Purpose

The authoritative artifact contract for the six-layer machine envelope. Each layer below gives its **required fields** and a **copy-ready template**. Emit complete files from repo evidence — never a stub. Any field you cannot ground in the repo is labeled `Unknown`, never fabricated.

## Layer 1 — Identity & Capabilities

Three machine files under `.ai/`. Declare boundaries, tiers, and interfaces.

`.ai/manifest.yaml`:
```yaml
schema_version: "1.0"
repository:
  id: <slug>
  name: <Human Name>
  type: ai-service | service | library | job
  lifecycle: production | beta | experimental
  criticality: tier-1 | tier-2 | tier-3
  description: >
    <what this repo coordinates/owns>
ownership:
  accountable_team: <team>
  technical_owner: <role/team>
  operational_owner: <production ops team>   # RED LINE: required, non-empty
boundaries:
  owns: [<capability>, ...]
  does_not_own: [<capability>, ...]
interfaces:
  inbound:
    - { type: http|event|grpc, contract: <path to schema> }
  outbound:
    - { type: http|event|grpc, contract: <path to schema> }
deployment:
  rollback:                                   # RED LINE: a machine rollback target must exist
    strategy: inverse-patch | blue-green | image-pin
    command: <dry-runnable command, e.g. scripts/rollback --dry-run>
    verified: true | false
```

`.ai/repository-map.yaml`:
```yaml
domains:
  <domain>:
    paths: [ src/<domain>/ ]
    purpose: <one line>
    owner: <team>
    public_surface: true | false
    depends_on: [ <domain>, ... ]
    invariants: [ "<must-remain-true>", ... ]
```
Verification: `paths` must align with real code imports (100% structural alignment is the gate metric).

`.ai/constraints.yaml`:
```yaml
latency: { request_budget_ms: <n>, reserve_ms: <n> }
data:
  prohibited_in_logs: [ raw_prompt, access_token, customer_email ]   # never log these
ai:
  prompt_versioning_required: true
  maximum_context_tokens: <n>
```

## Layer 2 — Architectural Truth (verification block)

Every structural doc (`ARCHITECTURE.md`, layer files, ADRs) carries this block. Unverifiable claims are `Unknown`.
```yaml
document_status:
  last_verified: <YYYY-MM-DD>
  verified_against:
    - commit: <sha>
    - environment: production | staging
    - dashboard: <name | Unknown>
  confidence: high | medium | low
  owner: <team>
```

## Layer 3 — Change Control (AGENTS.md + Task Contracts)

`AGENTS.md` states mission, the Authority Order cascade, and prohibited behaviors. Every mutation is preceded by a Task Contract:
```yaml
task: { id: <ID>, title: <title>, type: behavior-change | refactor | fix }
intent: { problem: <...>, desired_outcome: <...> }
scope:
  allowed: [ src/<area>/, tests/<area>/ ]
  prohibited: [ public API schema changes, database migrations ]
constraints:
  latency: { p99_ms: <n> }
  compatibility: { backward_compatible: true }
```

## Layer 4 — Verification & Evidence (validation classes)

Classify every test by what it *proves*, and give the machine command:

| Validation Class | Proves | Command (example) |
|---|---|---|
| Static Analysis | syntax, typing, style | `make lint` |
| Unit | local component correctness | `make test-unit` |
| Contract | inter-service schema compatibility | `make test-contract` |
| Integration | networked/resource interactions | `make test-integration` |
| Evaluation | probabilistic AI quality/safety thresholds | `make evaluate` |
| Performance | latency/memory/resource limits | `make test-perf` |
| Resilience | bounded degradation under fault injection | `make test-resilience` |

## Layer 5 — Operational Ownership (alert → runbook, 1:1)

Every alert pairs with a runbook that **resolves to a real file**:
```yaml
alert:
  name: <AlertName>
  severity: page | ticket
  owner: <sre team>
  signal: <metric>
  condition: "<expr>"
  runbook: docs/runbooks/<name>.md      # RED LINE: must resolve
  dashboard: <name>
  rollback_candidate: true | false
```

## Layer 6 — Transition State (technical-debt ledger)

Signed ledger with remediation targets; disclose, do not hide:
```yaml
debt:
  - id: TD-<n>
    title: <one line>
    origin: <why it exists>
    affected_paths: [ src/<area>/ ]
    consequences: [ "<measurable impact>" ]
    urgency_triggers: [ "<condition>" ]
    target_state: <desired end state>
    owner: <team>
    status: accepted | scheduled | mitigating
```

## AI Model & Prompt Registries (ai-service only)

```yaml
models:
  <role>: { purpose: <...>, routing: { primary: <p/m>, fallback: [<p/m>] }, timeout_ms: <n>, eval_baseline: evals/baselines/<...>.json }
prompts:
  <name>: { version: <semver>, path: prompts/system/<...>.md, output_schema: schemas/prompts/<...>.json, eval_suite: evals/cases/<...>/ }
```
RED LINE: every non-deterministic feature (each `prompts.*` / model role) must reference an `eval_suite`/`eval_baseline` that resolves.

## Role Matrix (mutation control)

No agent validates its own work.
- **Architect** — parses intent, builds a change strategy, verifies constraints. *Prohibited from writing implementation or approving code.*
- **Implementer** — writes minimal functional code, registers plan deviations. *Prohibited from grading its own quality.*
- **Reviewer/Test** — independent integration config, diff-for-scope-creep, runs assertions. *Prohibited from writing feature logic.*
- **Specialist (Security/Perf)** — triggered on auth/prompt-schema/hot-path changes to check data safety and latency-regression budgets.

Assembly: `Task Intake → Architect → Implementer → Reviewer/Test → Security/Perf → Gate Check`.
