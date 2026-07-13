<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Expertise Extraction Framework

## Purpose

Create the missing compiler brain: extract compressed expertise before designing a skill. This is mandatory for any skill that claims smart, exemplary, 10x, low-drift, high-autonomy, or domain-expert quality.

## Non-Negotiable Rule

Summarization is not expertise extraction. A summary says what the source says. Expertise extraction identifies the few rules that change future decisions.

## Pipeline Position

```text
parse_source -> extract_expertise -> compress_expertise -> design_skill
```

Do not design `SKILL.md`, adapters, references, scripts, or output modes until this stage produces an `expertise_model`.

## Required `expertise_model.yaml`

```yaml
expertise_model:
  experts: []              # max 5, required
  doctrine: []             # max 10, required
  invariants: []           # max 10, required
  authority_hierarchy: []  # max 5, required
  activation_signals: []   # max 5, required
  reject_signals: []       # max 5, required
  adapters: []             # max 3, required; may be [] only when explicitly not needed with rationale
  failure_modes: []        # max 5, required
  leverage_points: []      # max 5, required
```

## Extraction Rules

- Experts: identify the operator roles represented by the source, named or inferred.
- Doctrine: extract durable operating beliefs, not slogans.
- Invariants: extract rules that must remain true across contexts.
- Authority hierarchy: rank sources and rules for conflict resolution.
- Activation signals: concrete triggers, file patterns, task phrases, artifacts, or tool contexts.
- Reject signals: near-misses where activation would be wrong.
- Adapters: create only when context changes decisions, evidence, workflow, or output shape.
- Failure modes: name how the skill misfires and attach prevention rules.
- Leverage points: identify reusable behavior that compounds quality or reduces future drag.

## Compression Test

Keep a rule only if it changes at least one of:

- activation
- conflict resolution
- source selection
- output shape
- validation gate
- adapter routing
- refusal/fail-closed behavior
- next-action priority

Delete rules that only sound smart.

## Fail-Closed Conditions

Classify as `strong`, not `exemplary`, when:

- no reject signals can be extracted
- authority hierarchy is missing
- expert heuristics are generic checklist items
- adapters exist but change no decision rules
- failure modes lack prevention rules
- leverage points are vague or unscored
- intelligence report cannot be generated
