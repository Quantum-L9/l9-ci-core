<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: binding_runtime_directive
tags: [skill, compiler, directive, enforcement, kernel, mandatory]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-04
/L9_META -->

# Binding Runtime Directives

## Purpose

This file defines the **mandatory kernel integration layer** for the skill compiler. Any agent executing this skill MUST load and enforce the directives listed below during every compilation workflow. These are not optional references — they are binding constraints that override default behavior when their activation conditions are met.

## Enforcement Mechanism

The compiler MUST:
1. Load this file at the start of every build/rebuild/package/smart/exemplary workflow.
2. Evaluate which directives are activated by the current request.
3. Apply all activated directives as hard constraints during compilation.
4. Report which directives were activated in the delivery response.
5. Fail closed if a directive is violated — do not proceed past the violation.

## Directive Registry

| Directive | Reference | Activates When |
|-----------|-----------|----------------|
| Build Quality | `kernel-build-quality.md` | Any `/build_zip`, build, rebuild, or package request |
| Skill Doctrine | `kernel-skill-doctrine.md` | Every compilation (always active) |
| Compounding Leverage | `kernel-compounding-leverage.md` | Every compilation (always active) |
| Platform Doctrine | `kernel-platform-doctrine.md` | Skill generates code, CI, environments, or agent contracts for L9 repos |
| Coding Stack | `kernel-coding-stack.md` | Skill generates Constellation Node code, handlers, or transport logic |

## Directive: Build Quality (Always Active for Builds)

**Source:** `references/kernel-build-quality.md`

When activated, the compiler MUST:
- Build the entire pack in one pass (no deferred work).
- Create only production-ready, complete files.
- Validate with real checks (no fake pass claims).
- Label unknowns explicitly.
- HALT if any artifact would be stub-only or scaffold-only.
- Return a zip bundle with download link.

## Directive: Skill Doctrine (Always Active)

**Source:** `references/kernel-skill-doctrine.md`

When activated, the compiler MUST:
- Enforce one job per prompt in every generated skill.
- Apply the Trigger Triad to every `description` field.
- Use progressive disclosure tiers (Discovery → Activation → Execution).
- Reject mega-prompts, promptless SKILL.md files, and nested references deeper than one level.
- Front-load intent in the first sentence of every skill body.
- Encode judgment as worked examples, not abstract principles.

## Directive: Compounding Leverage (Always Active)

**Source:** `references/kernel-compounding-leverage.md`

When activated, the compiler MUST:
- Evaluate whether each file, reference, script, or asset compounds future execution capacity.
- Score leverage before adding complexity (minimum 3.5 to approve).
- Reject or reframe additions scoring below 2.5.
- Identify future actions accelerated and existing assets strengthened.
- Prefer reusable systems over one-time effort.
- Not confuse busyness with leverage.

## Directive: Platform Doctrine (Conditional)

**Source:** `references/kernel-platform-doctrine.md`

When activated, the compiler MUST:
- Enforce `uv`, `pyright strict`, `ruff`, `pytest` as non-negotiable tooling.
- Require `AGENTS.md` and canonical commands in generated skills that produce repos.
- Apply the validation ladder (`format` → `lint` → `type` → `test` → `ci`).
- Enforce agent security: least privilege, named roles, audit logs.
- Require reproducibility: locked dependencies, deterministic environments.

## Directive: Coding Stack (Conditional)

**Source:** `references/kernel-coding-stack.md`

When activated, the compiler MUST:
- Enforce `TransportPacket` as the only wire format.
- Reject any generated code containing `PacketEnvelope` imports.
- Enforce handler signature: `async def handler(packet: TransportPacket) -> TransportPacket`.
- Enforce Gate-only egress (no direct node-to-node calls).
- Enforce immutability: `derive()` for semantic changes, `with_hop()` for observational.
- Reject `eval()`, `exec()`, `compile()`, `yaml.load()` in generated code.
- Enforce `L9_META` headers on all generated files.

## Activation Report Contract

Every delivery response MUST include:

```yaml
binding_directives_applied:
  build_quality: active | not_applicable
  skill_doctrine: active  # always
  compounding_leverage: active  # always
  platform_doctrine: active | not_applicable
  coding_stack: active | not_applicable
  violations: ["none" | "{violation description}"]
```

## Violation Handling

If a binding directive is violated during compilation:
1. The compiler MUST stop and identify the violation.
2. The violation MUST be logged in the delivery response.
3. The compiler MUST fix the violation before proceeding.
4. If the violation cannot be fixed, the compiler MUST fail closed and report the blocker.

Violations of binding directives are treated as protocol violations per `enforcement-gates.md`.
