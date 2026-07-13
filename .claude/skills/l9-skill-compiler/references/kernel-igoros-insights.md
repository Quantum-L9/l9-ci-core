<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: convergence_kernel
tags: [skill, convergence, scoped_hydration, bounded_execution, meaning_compression]
owner: igor_beylin
status: active
version: 1.1.1
updated: 2026-06-06
/L9_META -->

# IgorOS Insights Kernel

## Purpose

Use this kernel to keep Skill packs bounded, modular, and execution-oriented.

## Core Principles

### Scoped Hydration

Load only the context needed for the current task.

In Skill design, this means:

- keep `SKILL.md` lean
- move detail into references
- load kernels only when relevant
- avoid forcing every future task to carry every rule

### Bounded Execution

Prefer a single clear execution route over sprawling optionality.

In Skill design, this means:

- define output modes
- route resources deliberately
- avoid multi-purpose junk drawers
- split Skills when one pack becomes too broad

### Meaning Compression

Retain patterns, rules, and operating logic instead of raw transcripts.

In Skill design, this means:

- compress uploaded prompts into behavior
- preserve intent over wording
- turn repeated doctrine into contracts
- delete source clutter after extraction

### Recursive Correction

Audit, fix, and harden until the design stops changing materially.

In Skill design, this means:

- validate after build
- remove drift
- collapse duplicates
- stop when added detail no longer improves execution

### Operational Convergence

The Skill is done when it can reliably execute its purpose without expanding its own scope.

## Validation Gates

- Only needed context is loaded.
- Execution path is bounded.
- Meaning is compressed.
- Duplicates are removed.
- Final structure converges.
