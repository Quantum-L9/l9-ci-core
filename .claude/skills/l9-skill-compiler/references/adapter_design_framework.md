<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Adapter Design Framework

## Purpose

Keep skills adaptive without turning every domain into global law.

## Adapter Creation Rule

Create an adapter only when a context changes at least one:

- authority order
- validation gates
- forbidden moves
- evidence requirements
- output shape
- tool or connector path
- failure modes

## Required Adapter Shape

```yaml
adapter:
  name: string
  load_when: []
  reject_when: []
  changes:
    authority_order: []
    validation_gates: []
    output_shape: []
    forbidden_moves: []
  does_not_change: []
```

## Rules

- Core skill remains default.
- Domain doctrine is conditional unless the whole skill is domain-specific.
- Max three default adapters unless user explicitly asks for more.
- Delete adapters that only add vocabulary or flavor.
- Adapter must explain what changes and why.

## Fail Conditions

Adapter architecture fails when:

- a domain adapter is always loaded without trigger
- adapter has no different decision rules
- adapter duplicates core behavior
- adapter makes unrelated tasks obey narrow doctrine
