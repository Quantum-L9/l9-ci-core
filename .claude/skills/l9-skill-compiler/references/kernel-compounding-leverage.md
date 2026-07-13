<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: compounding_kernel
tags: [skill, leverage, compounding, scoring, decision_rules]
owner: igor_beylin
status: active
version: 1.1.1
updated: 2026-06-06
/L9_META -->

# Compounding Leverage Kernel

## Cardinal Rule

A move is high quality when it improves future moves.

## Use When

Consult when deciding:

- whether to build one Skill or several
- whether to add a reference, script, asset, or validation layer
- whether a rule belongs in `SKILL.md` or a reference
- whether complexity adds durable reuse or recurring drag
- whether a proposed Skill compounds future execution capacity

## Hard Rules

- Evaluate whether the action compounds before committing.
- Identify future actions made easier, faster, clearer, safer, or more powerful.
- Identify existing assets, routines, systems, or relationships strengthened.
- Prefer reusable systems over one-time effort.
- Prefer moves that solve multiple bottlenecks at once.
- Preserve energy, attention, trust, optionality, and execution capacity.
- Label unverifiable leverage claims as `Unknown`.
- Do not confuse busyness with leverage.
- Do not add recurring maintenance without clear compounding return.
- Do not choose impressive complexity over durable advantage.

## Scoring

Score each dimension from 0 to 5 and apply weights:

```yaml
future_action_acceleration: 0.22
existing_asset_amplification: 0.20
reusable_system_value: 0.18
multi_domain_benefit: 0.14
optionality_gain: 0.10
energy_load_inverse: 0.08
social_or_network_gain: 0.08
```

Thresholds:

```yaml
reject_or_reframe_below: 2.5
approve_minimum: 3.5
prioritize_minimum: 4.0
life_architecture_candidate: 4.5
```

## Decision Rules

Approve when:

- leverage score is at least 3.5
- future action acceleration is identified
- current asset or system strengthened
- reusable output identified

Defer when:

- leverage score is at least 2.5
- leverage score is below 3.5
- useful but not sequence-optimal

Reject or reframe when:

- leverage score is below 2.5
- no future acceleration exists
- no reusable output exists
- recurring drag appears without multiplier
- energy cost exceeds compounding return

## Output Contract

When leverage analysis is explicitly requested, return:

```yaml
leverage_score: number
decision: approve | defer | reject_or_reframe
future_actions_accelerated:
  - item
current_assets_strengthened:
  - item
reusable_outputs_created:
  - item
multi_domain_benefits:
  - item
recurring_drag_risk: low | medium | high
unknowns:
  - item
```

## Validation Gates

- Future action acceleration declared.
- Current asset amplification declared.
- Reusable output declared.
- Multi-domain benefit checked.
- Recurring drag bounded.
- Energy cost checked.
- Unknowns declared.
