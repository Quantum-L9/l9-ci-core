<!-- L9_META
l9_schema: 1
parent: l9-devpack-compiler
layer: reference
role: enforcement_gates
tags: [dpk, enforcement, validation, artifacts, protocol-violation]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-15
/L9_META -->

# Enforcement Gates (Compiler Runtime Layer)

## Purpose

Prevent the compiler from skipping its own workflow steps by requiring a concrete artifact at each stage. Rules without enforcement are suggestions. Each gate defines the **proof-of-compliance** artifact the compiler MUST produce before advancing. A missing/invalid artifact means STOP.

## Gate Map

```text
Step 1 [GATE A] Step 2 [GATE B] Step 3 [GATE C] Step 4 [GATE D] Step 5 [GATE E] Step 6 [GATE F] Step 7 [GATE G]
```

## Gate A: Repo Parsed (Step 1)
```yaml
repo_parse:
  languages: [ ... ]
  entrypoints: [ ... ]
  services: [ ... ]
  interfaces: [ ... ]           # inbound/outbound, with contract paths or Unknown
  tests_present: true | false
  declared_owners: [ ... | "Unknown" ]
  unknowns: [ ... | "none" ]
```
Validation: objective non-empty; every unverifiable fact listed under `unknowns`.
**STOP if:** no source available → nothing to compile.

## Gate B: Expertise Extracted (Step 2 — exemplary builds)
```yaml
expertise_model: { experts, doctrine, invariants, authority_hierarchy,
  activation_signals, reject_signals, adapters, failure_modes, leverage_points }
```
Validation: behavior-changing rules (not summaries); caps respected (activation/reject ≤5, adapters ≤3).
**STOP if:** expertise cannot be extracted honestly → classify `strong`, not `exemplary`.

## Gate C: Layers Designed (Step 3)
```yaml
layer_plan:
  layer_1: { manifest: planned|na, repository_map: planned, constraints: planned }
  layer_2: verification_blocks_for: [ docs... ]
  layer_3: { agents_md: planned, task_contracts: [...] }
  layer_4: validation_classes: [ lint, unit, ... ]
  layer_5: alerts: [ {name, runbook} ... ]
  layer_6: debt_ledger: planned
  adapters_applied: [ non_ai_service | monorepo | greenfield | none ]
```
Validation: every planned artifact traces to repo evidence or is marked `Unknown`; no fabricated owners/contracts.

## Gate D: Envelope Emitted (Step 4)
```yaml
emit_manifest:
  files_written: {int}
  stubs_remaining: 0
  todos_remaining: 0
  verification_blocks_present: true
  every_file_complete: true
```
Validation: `stubs_remaining == 0`; every structural doc has a verification block or labeled Unknown.
**STOP if:** any stub/TODO/placeholder remains.

## Gate E: Execution Package Complete (Step 5)
```yaml
execution_package_check:
  repository_access: present
  environment_access: present
  scoped_credentials: present
  authoritative_contracts: present
  work_queue: present
  validation_commands: present
  stop_conditions: present
  unknown_components: [ ... | "none" ]   # each Unknown must appear in stop_conditions
```
Validation: all seven present; each `Unknown` component has a matching stop condition.
**STOP if:** a component is missing (not merely Unknown) → request it, do not invent.

## Gate F: Scored & Red-Lined (Step 6)
```yaml
score_report:
  total: {0-100}
  band: operable | conditional | blocked
  red_lines: { ops_owner: pass|fail, rollback: pass|fail, eval_suite: pass|fail, runbook_links: pass|fail }
  validator_run: "scripts/validate_devpack.py <repo>: {exit}"
```
Validation: `validate_devpack.py` actually ran; if any red-line `fail` → band MUST be `blocked`.
**STOP if:** band `blocked` is presented as operable → protocol violation.

## Gate G: Packaged & Delivered (Step 7)
```yaml
package_record:
  artifacts: [ ... ]
  score: {int}
  verdict: operable | conditional | blocked
  next_action: "<single highest-leverage action>"
  unknowns: [ ... | "none" ]
```

## Protocol Violation Detection

- Advance a gate without its artifact → **gate-skip**
- Claim `operable` with a red-line `fail` → **fake-readiness**
- Emit a layer with an invented (non-evidenced) owner/contract → **hallucinated-contract**
- Invent env/creds/contracts instead of stopping → **infra-invention**
- Ship a pack with `stubs_remaining > 0` → **stub-in-pack**

Violations MUST be logged in the delivery response and fed to the after-use improvement hook.
