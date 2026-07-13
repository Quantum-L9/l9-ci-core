<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: skill_doctrine_kernel
tags: [skill, doctrine, context_engineering, modularity, trigger_triad]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-04
/L9_META -->

# Skill Doctrine Kernel

## Cardinal Rule

Context engineering is the organizing principle for elite practitioners: providing all the context for the task to be plausibly solvable by the LLM. Skills encode procedure, not judgment. Judgment lives in the model; the skill provides procedural scaffolding and domain constraints.

## Use When

Consult when designing skill structure, writing `SKILL.md` descriptions, defining triggers, or organizing prompt libraries.

## Modularity Principles

- **One job per prompt, always.** Single-goal prompts achieve 89% satisfaction; multi-goal prompts drop to 41%.
- **DRY across prompts:** Shared context lives in `references/` files loaded on demand, not copy-pasted across multiple `SKILL.md` files.
- **Variable convention:** Use `{{VARIABLE_NAME}}` consistently and document in the frontmatter `inputs:` field.
- **Chain only when necessary:** Chain when step N output is inherently step N+1 input AND intermediate outputs are valuable for debugging.

## The Trigger Triad

The description field is the highest-leverage line in the entire file. It must contain:
1. **The capability:** what the skill produces (verb + object)
2. **The trigger conditions:** explicit "Use when..." clause
3. **The user's vocabulary:** literal phrases a user might type

## Progressive Disclosure Tiers

1. **Tier 1 — Discovery:** Name + description only (frontmatter).
2. **Tier 2 — Activation:** Full `SKILL.md` body loaded when triggered.
3. **Tier 3 — Execution:** `scripts/`, `references/`, `assets/` loaded on demand.

## Opinionated Authoring Rules

- Kill the wall-of-text prompt. Front-load intent in the first sentence.
- Encode judgment as worked input/output examples, not abstract principles.
- Maintain eval-as-maintenance: evals are the ongoing definition of "this prompt still works."

## Anti-Patterns to Kill on Sight

- **The mega-prompt:** Token waste, impossible to test, breaks silently.
- **Promptless SKILL.md:** "I'll fill it in later" skill with no body.
- **No eval, just vibes:** Vibe-check development trap.
- **Nested SKILL.md references:** All reference files should link one level deep from `SKILL.md`.
- **Time-sensitive content in skill bodies:** Causes prompt rot. Use "Old Patterns" collapsible sections instead.
