<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Exemplary Skill Contract

## Canonical Status

This file resolves the naming contract. The active detailed contract is `smart-exemplary-skill-contract.md`. This file is the canonical alias required by the compiler spec and must be linked from `SKILL.md`.

## Required Capabilities

```yaml
requirements:
  activation_precision:
    required: true
  adapter_architecture:
    required: true
  evidence_hierarchy:
    required: true
  first_order_reasoning:
    required: true
  doctrine_extraction:
    required: true
  self_improvement:
    required: true
  leverage_score:
    required: true
  trigger_false_positive_rate:
    target: defined_and_measured_or_fail_closed
  signal_specificity:
    target: high
```

## Build Phase Requirement

```text
parse_source
→ extract_expertise
→ compress_expertise
→ design_skill
→ validate
→ package
```

`extract_expertise` is mandatory for any claimed exemplary tier. Use `expertise_extraction_framework.md` for the phase rules. Use `validate_exemplary_skill.py` for deterministic fail-closed validation.

## Tier Rule

A skill may be called `exemplary` only when all required gates pass with evidence. If a gate is missing, unmeasured, documentation-only, or Unknown, the maximum allowed tier is `strong`.
