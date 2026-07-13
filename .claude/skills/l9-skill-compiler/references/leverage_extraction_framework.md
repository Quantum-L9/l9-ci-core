<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Leverage Extraction Framework

## Purpose

Force the compiler to identify the few behaviors that compound future output quality.

## Required Leverage Model

```yaml
leverage_model:
  leverage_points: []
  leverage_scores: []
  compounding_advantage: string
  reusable_advantage: string
  lowest_complexity_highest_gain_move: string
```

## Score Dimensions

Each leverage point receives 0-5 scores:

- future_action_acceleration
- output_quality_gain
- drift_reduction
- reuse_across_tasks
- implementation_simplicity

Total score = first four dimensions + implementation_simplicity.

## Rules

- Prefer the smallest high-leverage rule over a broad checklist.
- A leverage point must change future behavior, not just make docs prettier.
- If leverage cannot be explained, do not include it.

## Exemplary Threshold

At least one leverage point must score 18 or higher out of 25 and must map to a concrete generated skill behavior.
