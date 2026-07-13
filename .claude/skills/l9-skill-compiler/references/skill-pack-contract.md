<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: pack_contract
tags: [skill, standalone_protocol, structure, packaging, validation]
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Skill Pack Contract

## Purpose

This is the authoritative standalone protocol for creating, analyzing, rebuilding, validating, and packaging Skill packs.

Do not rely on any external Skill creator workflow. Use this file as the base protocol and use other references only for specialized rules. When the user asks for exemplary, smarter, 10x, low-drift, or high-autonomy skills, apply `exemplary_skill_contract.md`, `smart-exemplary-skill-contract.md`, and the expertise extraction frameworks. Produce `expertise_model.yaml` and `skill_intelligence_report.yaml` before claiming exemplary quality.

## Required Structure

Every complete Skill must include:

```text
skill-name/
└── SKILL.md
```

Optional folders:

```text
references/
scripts/
assets/
```

Create optional folders only when they provide reusable value.

**PlasticOS repo:** After creating a skill, wire via global **`l9-wire-skill-into-repo`** and `.claude/adapters/plasticos-repo-wiring.md`. Do **not** create `agents/openai.yaml`.

## SKILL.md Contract

`SKILL.md` is the control plane.

Required ChatGPT-compatible frontmatter:

```yaml
name: skill-name
description: lowercase trigger description
```

Rules:

- `name` must be short, lowercase, and hyphen-separated.
- `description` must be lowercase.
- `description` must state what the Skill does.
- `description` must state when the Skill should trigger.
- Use only `name` and `description` for ChatGPT-compatible Skill frontmatter unless the user explicitly targets a different platform schema.
- Do not bury trigger logic only in the body.

Body should include:

- purpose
- compact workflow
- behavior rules
- resource map
- validation requirements
- failure handling

Body should exclude:

- raw source dumps
- repeated reference content
- broad theory
- irrelevant examples
- stale project baggage
- unsupported capability claims

## SKILL.md Metadata

Single YAML frontmatter — discovery and audit in one block:

```yaml
---
name: skill-name
description: lowercase trigger description
skill_schema: 1
layer: control_plane
role: skill_entrypoint
tags: [tag1, tag2]
owner: igor_beylin
status: active
version: 1.0.0
updated: YYYY-MM-DD
---
```

Rules:

- `name` must match directory name; do not duplicate as `origin`.
- `description` must be lowercase with explicit triggers.
- Do not add a separate `SKILL_META` HTML comment after frontmatter.

## references/ Usage

Use `references/` for information that should be loaded only when needed:

- schemas
- policies
- examples
- domain rules
- long checklists
- API notes
- connector notes
- compressed kernels
- standalone protocols

Every reference must be linked from `SKILL.md`.

## scripts/ Usage

Use `scripts/` only for deterministic repeatable operations:

- validation
- packaging helpers
- parsing
- conversion
- file transformation
- deterministic extraction

Do not create scripts for work the model can reliably perform in text. Every script must be useful, wired into instructions, and testable.

## assets/ Usage

Use `assets/` for reusable final-output materials:

- templates
- static boilerplate
- images
- sample output materials
- non-reasoning reusable files

Do not use assets as hidden instruction memory.

## Build Workflow

Each step produces a required gate artifact (see [references/enforcement-gates.md](references/enforcement-gates.md)). The compiler MUST NOT advance past a gate without producing its artifact. Skipping a gate is a protocol violation.

1. Clarify only when blocked.
2. Parse source material. → **Gate A: source_parse artifact.**
3. If exemplary quality is requested, run `extract_expertise` between parsing and design.
4. Compress expertise into `expertise_model.yaml`: experts, doctrine, invariants, authority hierarchy, activation signals, reject signals, adapters, failure modes, and leverage points. → **Gate B: expertise_model artifact** (exemplary/smart only).
5. Apply first-order and compounding-leverage filters.
6. Design the Skill structure from the expertise model, not from raw source bulk. → **Gate C: file_tree artifact.**
7. Build complete files. → **Gate D: build_manifest artifact (stubs=0, todos=0).**
8. Generate `skill_intelligence_report.yaml` for smart/exemplary builds.
9. Validate all gates, including exemplary gates when applicable. → **Gate E: validation_report artifact.**
10. Wire into repo registries (PlasticOS: load global `l9-wire-skill-into-repo`). → **Gate F: wiring_report artifact** (when applicable).
11. Package only after validation passes (optional external zip export only). → **Gate G: package_record artifact.**

### 1. Clarify Only When Blocked

Proceed without questions when the source provides enough information.

Ask one focused question only when a missing input blocks correct design.

Required understanding:

- expected input
- expected output
- example user requests
- tools or connectors
- required artifacts
- quality bar

### 2. Parse Source Material

Extract:

- objective
- trigger conditions
- workflow steps
- reusable rules
- validation gates
- output formats
- constraints
- unknowns
- reusable resources

### 3. Filter for Leverage and Scope

Decide:

- one Skill or multiple Skills
- `SKILL.md` versus `references/`
- model-native instruction versus `scripts/`
- `assets/` versus `references/`
- keep versus remove

Reject:

- low-value complexity
- duplicated instructions
- cosmetic structure work that blocks functional value
- domain baggage not required by the Skill
- platform-specific assumptions unless required
- recurring maintenance without reusable return


### Mandatory Expertise Extraction for Exemplary Builds

Run this step when the user asks for a smarter, exemplary, 10x, low-drift, high-autonomy, or domain-expert skill.

Required phase order:

```text
parse_source -> extract_expertise -> compress_expertise -> design_skill -> run_exemplary_gate -> package
```

Required artifacts:

- `expertise_model.yaml`
- `skill_intelligence_report.yaml`

The intelligence layer must include activation model, reject signals, authority hierarchy, expert heuristics, conditional adapters, failure modes, leverage points, drift controls, and after-use improvement hook.

Do not claim exemplary tier unless `validate_exemplary_skill.py` or an equivalent deterministic validation report passes.

Use `canonical-smart-exemplary-spec.yaml` as the template. Do not add reference files, scripts, adapters, or checklists unless they pass the compression test: they must change future decisions, reduce drift, increase reuse, or enable deterministic validation.

If the intelligence layer cannot be extracted from available source evidence, classify the generated skill as `strong_skill`, not `exemplary_skill`.

### 4. Design the Skill

Produce:

- skill name
- trigger description
- file tree
- resource map
- execution workflow
- validation plan

### 5. Build Complete Files

Create or update:

- `SKILL.md` (single frontmatter block + body)
- needed references
- needed scripts
- needed assets
- `.claude/README.md`, `AGENTS.md`, relevant `.claude/agents/*.md` (PlasticOS repo wiring)

Remove:

- generated examples
- dummy scaffolds
- duplicate references
- stale assumptions
- unlinked resources
- unused files
- **`agents/openai.yaml` if present** — deprecated for this repo

### 6. Validate

Validate:

- SMART exemplary gates pass when exemplary quality was requested
- generated or internal `smart_exemplary_spec` follows the canonical template when applicable
- required files exist
- frontmatter is valid
- trigger description is strong
- metadata exists
- references are linked
- scripts are useful when present
- assets are appropriate when present
- unfinished markers are absent
- dummy scaffolds are absent
- package can ship as `skill.zip`

### 7. Package

Final distributable must be named exactly:

```text
skill.zip
```

Package the folder so the archive root contains the Skill directory, not only loose files.

Valid archive shape (optional external export):

```text
skill.zip
└── skill-name/
    ├── SKILL.md
    └── references/   # if present
```

If a platform validator exists, run it before packaging. If no validator exists, perform the validation checklist manually and create the archive with a standard zip utility. Do not claim a package exists unless an archive was actually created or the response is explicitly only a file-content build.

## Analyze Workflow

When analyzing an existing Skill, inspect:

- trigger quality
- scope boundaries
- required file structure
- reference routing
- script usefulness
- asset appropriateness
- metadata coverage
- duplicated logic
- dead resources
- validation readiness
- repo registry wiring (README, AGENTS.md, subagent preload lists)

Return strengths, gaps, risks, and edits in priority order.

## Rebuild Workflow

When rebuilding:

1. Preserve source intent.
2. Identify old versus new gaps.
3. Redesign the minimal viable structure.
4. Move large detail into references.
5. Remove dead or duplicated resources.
6. Rebuild full file contents.
7. Validate before presenting or packaging.

## Package Workflow

When packaging is requested:

1. Verify file tree.
2. Verify metadata.
3. Verify references are linked.
4. Verify generated examples and dummy scaffolds are absent.
5. Verify the archive root contains one Skill folder.
6. Verify package name is `skill.zip`.
7. Return the complete package, not a partial patch.

## Failure Rules

Fail closed when:

- source intent cannot be determined
- required files are unavailable
- requested connectors are unsupported
- output would require invented assets
- deterministic scripts are requested but cannot be tested
- validation fails
- package readiness cannot be established

When failing closed, return the blocker and the smallest safe next action.
