<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Evidence Hierarchy Framework

## Purpose

Prevent drift and fake certainty by forcing every exemplary skill to define which source wins when information conflicts.

## Required Authority Model

```yaml
authority_model:
  order:
    - latest_user_instruction
    - uploaded_or_connected_source_artifact
    - contracts_and_invariants
    - adrs_and_architecture
    - implementation_or_runtime_evidence
    - docs_and_comments
    - model_inference
  conflict_rule: higher_authority_wins
  unknown_rule: unknown_over_inference
```

## Enforcement

A skill may not claim exemplary if it lacks:

- explicit source ranking
- conflict handling
- Unknown handling
- stale-source handling when sources can age

## Staleness Handling

If source freshness matters, the generated skill must require one of:

- current connected source inspection
- uploaded file inspection
- web/current-source lookup when allowed
- `Unknown` when freshness cannot be verified

## Anti-Patterns

Reject:

- inference overruling evidence
- comments outranking contracts
- old docs treated as current without check
- undocumented assumptions presented as facts
- validation claims without inspected artifacts
