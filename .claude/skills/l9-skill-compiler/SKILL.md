---
name: l9-skill-compiler
description: compile prompts, sops, workflows, kernels, operating protocols, review systems, artifact generators, and domain playbooks into standalone zero-stub skill packs with smart exemplary intelligence extraction. use when the user asks to create, design, analyze, rebuild, validate, package, or improve reusable agent skills, chatgpt-compatible skill folders, model-agnostic skill packs, tool-using agent workflows, or skills that need sharper activation signals, authority order, expert heuristics, conditional adapters, drift control, and self-improvement hooks.
skill_schema: 1
layer: control_plane
role: skill_entrypoint
tags: [l9, skill, compiler, control_plane, zero_stub, standalone]
owner: igor_beylin
status: active
version: 3.3.0
updated: 2026-07-04
---

# Strict Skill Compiler

## Purpose

Compile prompts, SOPs, workflows, kernels, operating protocols, review systems, artifact generators, and domain playbooks into clean reusable Skill packs. When the user asks for higher quality, smarter behavior, reduced drift, or "exemplary" skills, run the mandatory expertise extraction and compression pipeline before designing files. Exemplary means compressed judgment, not extra bulk.

The generated Skill must stand alone. Do not assume the executing agent has any external Skill creator protocol, hidden conventions, prior memory, or platform-specific build workflow unless the user explicitly supplies it.

## Core Contract

| Mode | Output | Load |
|------|--------|------|
| discuss | Options, trade-offs | [references/output-modes.md](references/output-modes.md) |
| design | File tree, resource map | [references/file-contract.md](references/file-contract.md) |
| analyze | Gap report vs contract | [references/skill-pack-contract.md](references/skill-pack-contract.md) |
| build / rebuild | Complete skill pack | all refs + [build_execution_contract.md](references/build_execution_contract.md) + [validation-checklist.md](references/validation-checklist.md) |
| smart / exemplary | Skill with compressed intelligence layer | [references/smart-exemplary-skill-contract.md](references/smart-exemplary-skill-contract.md) + [references/canonical-smart-exemplary-spec.yaml](references/canonical-smart-exemplary-spec.yaml) |
| package / `/build_zip` | Archive-ready pack with real validation and zip link | [references/build_execution_contract.md](references/build_execution_contract.md) + validation-checklist |

Mandatory final step: **`l9-wire-skill-into-repo`**.

## Authority Order

1. Source material and explicit user objective.
2. [references/skill-pack-contract.md](references/skill-pack-contract.md) — standalone protocol.
3. [references/meta-standard.md](references/meta-standard.md) — metadata and frontmatter.
4. [references/binding-runtime-directives.md](references/binding-runtime-directives.md) — mandatory kernel enforcement layer.
5. Kernel references for reasoning, zero-stub, and leverage filters.
6. `Unknown` — fail closed; do not fabricate paths, tools, or assets.

## Operating Rules

- Preserve source intent, required outputs, scope, and constraints.
- Compress source material into operational behavior, not archived prose.
- Keep `SKILL.md` lean as the control plane.
- Formalize the complete base Skill protocol only in `references/skill-pack-contract.md`.
- Keep kernels modular and compressed in `references/`.
- **Apply binding runtime directives:** The compiler MUST consult and enforce the rules in `kernel-build-quality.md`, `kernel-skill-doctrine.md`, `kernel-platform-doctrine.md`, and `kernel-coding-stack.md` when applicable to the requested build.
- Treat prompts, kernels, contracts, checklists, and output modes as first-class operational primitives.
- Use scripts only when deterministic repeatable execution is explicitly useful.
- Use assets only for reusable final-output material.
- Do not invent connectors, tools, file paths, assets, commands, or dependencies.
- Do not ship dummy scaffolds, unfinished sections, unlinked files, or partial artifacts.
- When building, rebuilding, packaging, exporting, or handling `/build_zip`, load `references/build_execution_contract.md` and deliver files plus validation plus zip rather than a plan.
- Do not create `agents/openai.yaml` — wire new skills via global **`l9-wire-skill-into-repo`** (mandatory final step).
- Fail closed when correctness requires information that is missing and cannot be safely labeled `Unknown`.
- **Enforcement gates are mandatory.** Each workflow step produces a required artifact before the compiler advances. Load [references/enforcement-gates.md](references/enforcement-gates.md). Skipping a gate is a protocol violation.
- **Protocol violations must be reported.** If the compiler skips a gate or fabricates an artifact, it MUST log the violation in the delivery response.

## Compact Workflow

Each step produces a required gate artifact (see [references/enforcement-gates.md](references/enforcement-gates.md)). The compiler MUST NOT advance past a gate without producing its artifact.

0. **Load binding runtime directives** — read [references/binding-runtime-directives.md](references/binding-runtime-directives.md) and activate all applicable directives for this request. Report activated directives.
1. Parse the source into objective, scope, triggers, workflow, constraints, outputs, resources, risks, and unknowns. → **Produce Gate A artifact.**
2. If the user asks for smart, exemplary, 10x, low-drift, high-autonomy, or domain-expert quality, run the mandatory `extract_expertise` stage before design. Do not substitute summaries for expertise.
3. Compress the extracted expertise into the smallest behavior-changing intelligence model: expert roles, doctrine, invariants, authority hierarchy, activation/reject signals, adapters, failure modes, and leverage points. → **Produce Gate B artifact** (exemplary/smart mode only).
4. Apply first-order and compounding-leverage filters to choose the smallest structure with durable reuse.
5. Select mode: discuss, design, analyze, build, rebuild, smart/exemplary, or package.
6. Design the file tree and resource map from the expertise model, not from raw source bulk. Include only references, scripts, and adapters that change future behavior. → **Produce Gate C artifact.**
7. Build or revise complete files only. For build/rebuild/package requests, apply `references/build_execution_contract.md`: inspect inputs, generate complete files, validate honestly, create the zip, and return the link. → **Produce Gate D artifact.**
8. Generate `skill_intelligence_report.yaml` for every smart/exemplary build.
9. Run the exemplary gate before package. If any gate fails or is Unknown, classify the skill as `strong`, `developing`, or `failed`, never `exemplary`. → **Produce Gate E artifact.**
10. **Wire into repo registries** — load and execute global **`l9-wire-skill-into-repo`** (`~/.cursor/skills/l9-wire-skill-into-repo/SKILL.md`). Pass `skill-name`, `skill-path`, `description`, and `scope`. Load `.claude/adapters/plasticos-repo-wiring.md` when present in PlasticOS repos. → **Produce Gate F artifact** (when applicable).
11. Validate metadata, structure, exemplary gates when applicable, references, repo wiring, zero-stub gates, and package readiness.
12. Deliver the requested artifact and, when useful, one highest-leverage next prompt or next action. → **Produce Gate G artifact** (for package/zip requests).

### Mandatory Exemplary Pipeline

```text
parse_source
→ extract_expertise
→ compress_expertise
→ design_skill
→ run_exemplary_gate
→ package
```

`extract_expertise` is required for any claimed exemplary tier. The compiler must fail closed when the expertise model is missing or incomplete.

For the full standalone creation protocol, load `references/skill-pack-contract.md`.


## Extract Expertise Stage

Use this stage whenever the requested quality bar is smart, exemplary, 10x, low-drift, autonomous, domain-expert, or equivalent. Load `references/expertise_extraction_framework.md` first.

Required outputs before skill design:

- `expertise_model.yaml`: experts, doctrine, invariants, authority_hierarchy, activation_signals, reject_signals, adapters, failure_modes, leverage_points.
- `skill_intelligence_report.yaml`: activation model, authority model, expert heuristics, doctrine, invariants, adapter map, failure modes, leverage points, evidence hierarchy, exemplary gate results, tier decision.

Rules:

- Do not summarize source material and call it expertise.
- Do not design adapters before extracting the domain law that justifies them.
- Do not claim `tier: exemplary` unless `scripts/validate_exemplary_skill.py` passes or an equivalent deterministic gate report is produced.
- If a metric is described but not measured or inspected, mark it `Unknown` and fail exemplary classification.

## SMART Exemplary Discipline

Use this section only when the user asks for a smarter, exceptional, exemplary, 10x, low-drift, or domain-expert skill.

Mandatory behavior:

- Extract intelligence before adding files.
- Prefer fewer, stronger rules over broader checklists.
- Require reject signals, not only trigger signals.
- Require an authority order for conflict resolution.
- Extract expert heuristics as condition -> judgment -> action.
- Add adapters only when domain laws materially change behavior.
- Keep domain doctrine conditional unless the whole skill is domain-specific.
- Include a compact after-use improvement hook.
- Classify as `strong_skill`, not `exemplary_skill`, when the intelligence layer cannot be extracted honestly.

Hard limits by default: five strong activation signals, five reject signals, five authority rules, seven expert heuristics, three adapters, and five failure modes.

Load `references/smart-exemplary-skill-contract.md` for the full contract. Use `references/canonical-smart-exemplary-spec.yaml` as the template for the internal or emitted spec.

## Metadata Discipline

`SKILL.md` uses a **single YAML frontmatter block** for discovery + audit (see `references/meta-standard.md`).

Reference files in `references/` may use HTML-comment metadata blocks. Do not duplicate metadata across frontmatter and comments on `SKILL.md`.

For the metadata contract, load `references/meta-standard.md`.

## Resource Map

Load references only when relevant:

- [references/binding-runtime-directives.md](references/binding-runtime-directives.md): **MANDATORY** — binding kernel enforcement layer. Load ALWAYS at workflow start (Step 0). Defines which kernels activate and how violations are handled.
- [references/enforcement-gates.md](references/enforcement-gates.md): **step-level enforcement artifacts** — required proof-of-compliance at each workflow step. Load ALWAYS during build/rebuild/package.
- [references/project-adapters.md](references/project-adapters.md): repo-local wiring adapters — loaded by **`l9-wire-skill-into-repo`** Step 3.
- [references/skill-pack-contract.md](references/skill-pack-contract.md): complete standalone Skill creation, analysis, rebuild, validation, and packaging protocol.
- [references/meta-standard.md](references/meta-standard.md): file metadata and first-class primitive rules.
- [references/file-contract.md](references/file-contract.md): file and folder responsibilities, routing, and resource placement rules.
- [references/output-modes.md](references/output-modes.md): response contracts for discuss, design, analyze, build, rebuild, smart/exemplary, and package modes.
- [references/smart-exemplary-skill-contract.md](references/smart-exemplary-skill-contract.md): intelligence extraction, activation precision, authority order, expert heuristic, adapter, drift-control, and self-improvement rules for exceptional skills.
- [references/canonical-smart-exemplary-spec.yaml](references/canonical-smart-exemplary-spec.yaml): canonical template and guide SPEC YAML for generating the compressed intelligence layer before writing a smarter skill.
- [references/build_execution_contract.md](references/build_execution_contract.md): full-build, one-turn, no-stub, validation-backed zip packaging contract for build/rebuild/package and `/build_zip` requests.
- [references/validation-checklist.md](references/validation-checklist.md): final validation gates before presenting or packaging a Skill.
- [references/kernel-agent-state.md](references/kernel-agent-state.md): deterministic output discipline, no drift, fail-closed behavior, and explicit-write rules.
- [references/kernel-first-order-thinking.md](references/kernel-first-order-thinking.md): highest-leverage sequencing and five gates.
- [references/kernel-build-quality.md](references/kernel-build-quality.md): **BINDING DIRECTIVE** for `/build_zip` and full-build requests, enforcing enterprise-grade one-turn completion.
- [references/kernel-skill-doctrine.md](references/kernel-skill-doctrine.md): **BINDING DIRECTIVE** for context engineering, modularity, and the Trigger Triad.
- [references/kernel-platform-doctrine.md](references/kernel-platform-doctrine.md): **BINDING DIRECTIVE** for L9 platform invariants, typing, and CI governance.
- [references/kernel-coding-stack.md](references/kernel-coding-stack.md): **BINDING DIRECTIVE** for Constellation Node generation, enforcing `TransportPacket` exclusivity.
- [references/kernel-compounding-leverage.md](references/kernel-compounding-leverage.md): compounding leverage scoring and decision thresholds.
- [references/kernel-ynp-next-prompt.md](references/kernel-ynp-next-prompt.md): one-next-prompt discipline for reducing turns after deliverables.
- [references/kernel-zero-stub-build.md](references/kernel-zero-stub-build.md): complete-artifact enforcement and anti-scaffold checks.
- [references/kernel-reasoning-think-strategy.md](references/kernel-reasoning-think-strategy.md): objective-to-delivery reasoning flow.
- [references/kernel-igoros-insights.md](references/kernel-igoros-insights.md): scoped hydration, bounded execution, meaning compression, and operational convergence.
- [scripts/validate_smart_exemplary_spec.py](scripts/validate_smart_exemplary_spec.py): optional deterministic check for generated SMART exemplary spec structure when a spec YAML is created or edited.
- [scripts/validate_exemplary_skill.py](scripts/validate_exemplary_skill.py): mandatory deterministic check before claiming tier: exemplary.

## Validation

Before final delivery, validate against `references/validation-checklist.md`, apply `references/build_execution_contract.md` for build/package requests, run SMART exemplary gates when applicable, and confirm **`l9-wire-skill-into-repo`** completed successfully where repo wiring is in scope.

A Skill may not be considered complete unless required files exist, metadata is present, references are linked, **repo registries are updated via `l9-wire-skill-into-repo`**, trigger logic is strong, kernels are compressed, no dummy scaffolds remain, no `agents/openai.yaml` was created, and package readiness is confirmed.

## Failure Handling

When blocked:

- State the exact blocker.
- Label missing or unverifiable information as `Unknown`.
- Do not fabricate missing resources.
- Provide the smallest safe next action.
- If a complete artifact was requested but cannot be validated, do not present it as complete.
