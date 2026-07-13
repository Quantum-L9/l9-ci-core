<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: file_contract
tags: [skill, files, folders, routing, resources]
owner: igor_beylin
status: active
version: 2.0.0
updated: 2026-06-15
/L9_META -->

# File Contract

## Purpose

Use this contract to decide where content belongs and how resources should be routed.

## Routing Principle

Put each piece of content in the smallest file location that preserves reuse without bloating the control plane.

## File Responsibilities

### SKILL.md

Role: control plane.

Use for:

- trigger-backed operating instructions
- compact workflow
- behavior rules
- reference map
- validation path
- failure handling

Avoid:

- full source material
- long examples
- long checklists
- full kernel prose
- repeated reference content

`SKILL.md` also carries audit fields in the same YAML frontmatter block (`skill_schema`, `layer`, `role`, `tags`, `owner`, `status`, `version`, `updated`).

Do **not** create `agents/openai.yaml` or duplicate metadata in HTML comments on `SKILL.md`.

### Repo registries (PlasticOS)

After creating a skill, update:

| File | What to add |
|------|-------------|
| `.claude/README.md` | Skills table row |
| `AGENTS.md` | Agent Skills table row |
| `.claude/agents/*.md` | `skills:` preload list on relevant subagents |
| `CLAUDE.md` | References/Imports only if foundational |

### references/

Role: modular operational knowledge.

Use for:

- contracts
- kernels
- schemas
- checklists
- examples
- connector notes
- domain rules

Rules:

- Link every reference from `SKILL.md`.
- Keep references one level deep unless a larger pack explicitly needs deeper organization.
- Make each reference removable without breaking unrelated references.

### scripts/

Role: deterministic execution.

Use for:

- repeatable validation
- file conversion
- parsing
- packaging helpers
- deterministic transformations

Rules:

- Every script must be named in `SKILL.md` or a reference that `SKILL.md` links to.
- Every script must have a clear invocation path.
- Every script must be testable.
- Do not add scripts for ordinary text reasoning.

### assets/

Role: reusable output materials.

Use for:

- templates
- boilerplate
- static media
- reusable sample outputs

Rules:

- Assets are for output generation, not hidden reasoning.
- Large assets must be bounded by package limits.
- If an asset cannot carry internal metadata, add a sidecar metadata file.

## Resource Decision Table

| Content type | Destination |
|---|---|
| trigger logic | `SKILL.md` frontmatter description |
| version/audit metadata | `SKILL.md` YAML frontmatter (same block as `name`/`description`) |
| repo registration | `.claude/README.md`, `AGENTS.md`, `.claude/agents/` |
| short operating rule | `SKILL.md` |
| smart exemplary spec template | `references/canonical-smart-exemplary-spec.yaml` |
| intelligence extraction rules | `references/smart-exemplary-skill-contract.md` |
| long checklist | `references/` |
| reusable kernel | `references/` |
| deterministic validator | `scripts/` |
| document template | `assets/` |
| source archive | exclude or compress into references |
| examples used only sometimes | `references/` |
| visual or file template | `assets/` |

## Removal Rules

Remove any file that is:

- unlinked
- duplicated
- stale
- not useful for future execution
- not safely explainable in one sentence
- only present because an initializer created it
