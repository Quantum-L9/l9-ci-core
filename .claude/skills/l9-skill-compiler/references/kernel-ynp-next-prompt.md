<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: next_prompt_kernel
tags: [skill, next_prompt, ynp, velocity, minimum_turns]
owner: igor_beylin
status: active
version: 1.1.1
updated: 2026-06-06
/L9_META -->

# YNP Next Prompt Kernel

## Purpose

Use this kernel to produce one highest-leverage next prompt or next action when doing so reduces user turns and preserves execution momentum.

## Use When

Consult when:

- a Skill design needs immediate execution
- a build ends with a natural next validation or packaging step
- the user asks for a chain prompt
- the user wants maximum velocity or minimum turns
- several possible next moves exist but one clearly compounds progress

## Hard Rules

- Produce exactly one next prompt or next action when relevant.
- Preserve the current objective, scope, constraints, and required outputs.
- Do not introduce new requirements without source support.
- Do not branch into menus unless the user explicitly asks for options.
- Make the next prompt executable without reinterpretation.
- Target the bottleneck that most improves the next move.

## Output Shape

```yaml
next_prompt:
  objective: string
  instructions:
    - imperative_step
  acceptance_criteria:
    - binary_or_numeric_gate
```

## Validation Gates

- One next prompt only.
- Scope preserved.
- No invented requirements.
- Highest-leverage bottleneck targeted.
- Prompt is executable as written.
