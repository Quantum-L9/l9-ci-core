<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: reasoning_kernel
tags: [skill, reasoning, strategy, decomposition, synthesis]
owner: igor_beylin
status: active
version: 1.1.1
updated: 2026-06-06
/L9_META -->

# Reasoning Think Strategy Kernel

## Purpose

Use this kernel to reason through Skill compilation without drifting into broad theory.

## Flow

```text
objective -> context -> decomposition -> leverage -> strategy -> execution -> synthesis -> delivery
```

## Steps

### 1. Objective

Restate the task, success condition, beneficiary, urgency, and expected output.

### 2. Context

Identify domain, constraints, prior work, dependencies, and why the Skill matters now.

### 3. Decomposition

Break the source into triggers, workflows, rules, outputs, validation gates, resources, and unknowns.

### 4. Leverage

Identify what can be reused, compressed, scripted, referenced, or removed.

### 5. Strategy

Choose the simplest structure that preserves intent and improves future execution.

### 6. Execution

Build or revise complete files according to the selected output mode.

### 7. Synthesis

Summarize the resulting Skill posture, tradeoffs, and validation status.

### 8. Delivery

Return the requested artifact or contents with clear validation state and next action.

## Validation Gates

- Objective is clear.
- Context is bounded.
- Components are decomposed.
- Leverage is identified.
- Strategy matches scope.
- Execution output is complete.
- Delivery is usable.
