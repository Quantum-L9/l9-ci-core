<!-- L9_META
l9_schema: 1
parent: l9-devpack-compiler
layer: reference
role: spec_schema
tags: [dpk, spec, intent, machine-readable, no-pseudocode, context-efficient]
owner: igor_beylin
status: active
version: 1.3.0
updated: 2026-07-15
/L9_META -->

# Build Spec Schema (machine-readable intent)

## Purpose

The **optimal input** to the compiler is a spec of **facts**, not code. Instead of an agent burning its context window writing file bodies and pseudo-code it will only throw away, it emits a declarative spec — interfaces, invariants, commands, contracts, a file plan, a work queue, stop conditions — plus **first-class open decisions**. The compiler then populates the DPK-1.0 layers directly from those facts. Schema: [`schemas/spec.schema.json`](../schemas/spec.schema.json); worked example: [`schemas/spec.example.json`](../schemas/spec.example.json).

## Zero-stub is for BUILDING; a spec carries open work as DATA

A **stub** is fake content pretending something is done (a placeholder file, a `// TODO` body, `...`). That is banned in a *built* artifact. A **spec** legitimately records what is *not* done — but as structured data, never as a stub:

- unbuilt files → `file_plan[].status: build | extract | adapt | deferred` (path + purpose + contract, **no body**).
- unresolved choices → `open_decisions[]` (`question`, `options`, `default`, `blocking`, `owner`). A `blocking: true` open decision keeps the pack out of `operable` until resolved.
- behavior → `interfaces` + `invariants` + `commands` (the contract), **not pseudo-code**.

So: recording "this isn't built yet" as `status`/`open_decisions` is honest and required; writing a placeholder file that *looks* built is the stub the build gate rejects.

## Field → DPK artifact mapping

Every spec block maps to exactly one DPK-1.0 output — this is what "facts populate the manifest / repository-map / execution-package" means:

| Spec field | Populates |
|---|---|
| `meta`, `ownership`, `boundaries`, `interfaces`, `deployment` | `.ai/manifest.yaml` (L1) |
| `domains` | `.ai/repository-map.yaml` (L1) |
| `constraints` (budgets, prohibited_in_logs, governance, auth, allowlists) | `.ai/constraints.yaml` (L1) |
| `verification` | L2 verification block on structural docs |
| `authority_order`, `invariants` (as prohibitions/rules), task scope | `AGENTS.md` + task contracts (L3) |
| `commands` + `validation_classes` | L4 validation-class table |
| `observability.alerts[]` (`runbook_ref`) | L5 alert→runbook map |
| `debt`, `open_decisions` | L6 transition ledger |
| `environment`, `credentials`, `contracts`, `work_queue`, `commands`, `stop_conditions`, `repository access` | the **execution package** (7 components) |
| `entities`, `invariants` | repository-map invariants + data-model docs |
| `file_plan` | the build map (what to create, in what status) — **not** file contents |

## The full fact set

`meta · ownership · boundaries · interfaces · entities · invariants · commands · contracts · file_plan · domains · constraints · environment · credentials · deployment(+rollback) · observability · work_queue · acceptance · stop_conditions · open_decisions · debt · verification · authority_order · provenance`

Beyond the three you named (interfaces / invariants / commands), the compiler also needs: **contracts** (schemas by `$id`+path), **entities** (data model shapes + rules), **file_plan** (paths + build status, no bodies), **domains** (ownership map), **constraints** (budgets, prohibited-in-logs, governance, auth, allowlists), **environment** (runtime, services, bootstrap), **credentials** (env-var names only), **deployment.rollback** (dry-runnable command — the red line), **observability** (alerts + runbook refs), **work_queue** (phases + entry/exit criteria), **acceptance** (definition of done), **stop_conditions**, **open_decisions** (the TODO channel), **debt**, **verification** (commit/env/confidence), **authority_order**, and **provenance**.

## Reconciliation (in-scope repo): presence ≠ conformance

When the repo is available, do NOT re-plan what's already built — but do NOT trust that a file exists as proof it is correct or aligned with the spec. Run [`scripts/reconcile_spec.py`](../scripts/reconcile_spec.py) (`spec` + `--repo <root>`): it verifies each spec item and compresses the work queue to the **delta**, which saves tokens and turns deployment into a high-velocity delta apply.

Per-item verdicts:

| Verdict | Meaning | In remaining work? |
|---|---|---|
| `conformant` | present AND a deterministic conformance check passed (contract has `$id`, command wired in `package.json`, path matches) | dropped |
| `external` | owned by another repo (`contracts[].owner_repo`) | dropped |
| `deferred` | spec-deferred | dropped |
| `present_unverified` | present, but conformance is not statically decidable → **needs an agent/test; NOT done** | kept |
| `drifted` | present but the check FAILED, OR spec says `existing` yet the repo lacks it | kept (first) |
| `absent` | not built | kept |

**Doctrine:** nothing is marked done by presence alone. Existing code is *evidence to verify*, not proof of completion. A present-but-drifted artifact is a finding to reconcile, never a skipped item. Only `conformant`/`external`/`deferred` compress out; everything else stays as the delta a building agent works. `drifted` → exit 1 (spec↔repo inconsistency).

## Rules

- Reference contracts by `id`/`path`; never inline schema or code bodies.
- Credentials are **names only** (`^[A-Z][A-Z0-9_]*$`) — never values.
- A concrete code snippet in a source spec is mined for the **contract/invariant** it implies; a placeholder/`TODO` snippet becomes an `open_decision` or a `stop_condition`, not a file.
- A snippet that conflicts with a higher-authority contract (SDK/public schema) is flagged, not enshrined (authority order).
- Validate a spec against `schemas/spec.schema.json` before compiling; a valid spec compiles to a pack with no invented infrastructure.
