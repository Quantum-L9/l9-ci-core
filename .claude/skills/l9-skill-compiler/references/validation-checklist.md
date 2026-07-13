<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: validation_contract
tags: [skill, validation, quality_gates, zero_stub, packaging]
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Validation Checklist

## Required Files

- [ ] `SKILL.md` exists.
- [ ] `SKILL.md` frontmatter includes `name`, `description`, and audit fields (`skill_schema`, `layer`, `role`, `tags`, `owner`, `status`, `version`, `updated`).
- [ ] No duplicate `SKILL_META` HTML comment on `SKILL.md`.
- [ ] Optional folders exist only when useful.
- [ ] Initializer-generated example files are absent.
- [ ] **No** `agents/openai.yaml` or `agents/` folder created.

## Repo Wiring (mandatory for project-scoped skills)

- [ ] Global skill **`l9-wire-skill-into-repo`** executed and reported PASS.
- [ ] Row added to `.claude/README.md` (L9 Global or Project Skills table as appropriate).
- [ ] Row added to `AGENTS.md` Agent Skills table (if table exists).
- [ ] Relevant `.claude/agents/*.md` updated with `skills:` preload if subagents should delegate with it.
- [ ] `name` in frontmatter matches directory name.
- [ ] Subagent preload decision documented if no subagent was updated.

Load global **`l9-wire-skill-into-repo`**; load `.claude/adapters/plasticos-repo-wiring.md` when compiling in PlasticOS.

## Frontmatter

- [ ] `SKILL.md` frontmatter contains only `name` and `description` for ChatGPT-compatible Skills.
- [ ] `name` is lowercase.
- [ ] `name` is short.
- [ ] `name` is hyphen-separated.
- [ ] `description` is lowercase.
- [ ] `description` explains what the Skill does.
- [ ] `description` explains when the Skill should trigger.
- [ ] Trigger logic is not hidden only in the body.

## Metadata

- [ ] Reference files include HTML-comment metadata unless format cannot support comments.
- [ ] Any metadata sidecar clearly identifies the asset it describes.
- [ ] Every metadata block includes all required fields.
- [ ] `layer` matches file location and purpose.
- [ ] `role` matches file behavior.
- [ ] Tags improve search.
- [ ] Purpose is short and accurate.
- [ ] Metadata contains no secrets.
- [ ] Metadata identifies the file instead of storing large doctrine.

## SKILL.md Body

- [ ] Instructions are operational.
- [ ] Workflow is compact.
- [ ] Resource navigation is clear.
- [ ] Validation requirements are included.
- [ ] Failure handling is included.
- [ ] Full base protocol is not duplicated outside `references/skill-pack-contract.md`.
- [ ] Kernel content is not copied in full.

## References

- [ ] Every reference file is linked from `SKILL.md`.
- [ ] Each reference has a distinct purpose.
- [ ] Kernels are compressed.
- [ ] Long checklists live in references, not `SKILL.md`.
- [ ] No reference acts as hidden memory.

## Scripts

- [ ] Every script performs deterministic useful work.
- [ ] Every script has a clear invocation path.
- [ ] Every script is testable.
- [ ] No script is present solely because a template generated it.
- [ ] No script is used for ordinary text reasoning.

## Assets

- [ ] Assets are reusable output materials only.
- [ ] Assets are not hidden instructions.
- [ ] Assets are linked or explained by the control plane or references.
- [ ] Large assets stay within package limits.
- [ ] Assets that cannot contain metadata have sidecar metadata.

## Zero-Stub Gates

Reject the Skill if it contains:

- [ ] incomplete required files
- [ ] unfinished-task markers
- [ ] dummy scaffolds
- [ ] pretend scripts
- [ ] unsupported capability claims
- [ ] invented connectors
- [ ] invented file paths
- [ ] unused example files
- [ ] unlinked references
- [ ] bloated control plane
- [ ] partial artifact delivery
- [ ] `agents/openai.yaml` created (use `SKILL.md` metadata + repo wiring instead)

## SMART Exemplary Gates

Apply when the user asks for smarter, exemplary, 10x, low-drift, high-autonomy, or domain-expert skills.

- [ ] A `smart_exemplary_spec` was created internally or emitted when requested.
- [ ] Strong activation signals are concrete and capped.
- [ ] Reject signals are present.
- [ ] Authority order is present and resolves conflicts.
- [ ] Expert heuristics use condition -> judgment -> action.
- [ ] Adapters are conditional and state what decision rules they change.
- [ ] Domain doctrine is not global unless the skill is domain-specific.
- [ ] Failure modes have prevention rules.
- [ ] Self-improvement hook exists for reported bad runs or recurring corrections.
- [ ] No reference file exists without a load trigger and behavior change.
- [ ] No checklist item exists only for appearance.
- [ ] Classify as `strong_skill`, not `exemplary_skill`, when gates cannot be honestly satisfied.

Optional deterministic check: run `python scripts/validate_smart_exemplary_spec.py references/canonical-smart-exemplary-spec.yaml` or against a generated spec file.

## Design Principles (command / skill authoring)

Harvested from Suite-5 command standards — apply when compiling or reviewing skills:

- [ ] **Composable** — works alone and chains with related skills without hidden deps.
- [ ] **Fail fast** — prerequisites checked before long execution; clear error on missing input.
- [ ] **Idempotent when possible** — repeat runs safe; same inputs → same outcome.
- [ ] **Self-documenting output** — deliverable states what happened and suggested next step.
- [ ] **Progressive enhancement** — simple path works; advanced behavior optional via references/scripts.
- [ ] **Dry-run or validate step** before destructive ops when applicable.


## Exemplary Compiler Gates

Apply before claiming `tier: exemplary` for any generated skill.

- [ ] `extract_expertise` stage ran after `parse_source` and before `design_skill`.
- [ ] `expertise_model.yaml` exists or was emitted internally with all required fields.
- [ ] `skill_intelligence_report.yaml` exists for the compiled skill.
- [ ] Activation precision is measured, not merely described.
- [ ] False-positive risk is measured or marked Unknown and fails closed.
- [ ] Signal specificity score is high.
- [ ] Evidence hierarchy includes authority order, conflict handling, staleness handling, and Unknown handling.
- [ ] Doctrine extraction produced behavior-changing rules.
- [ ] Invariant extraction produced rules that must remain true.
- [ ] Expert heuristics use condition -> judgment -> action.
- [ ] Adapters are conditional and change decision rules.
- [ ] Failure modes include prevention rules.
- [ ] Leverage model includes explicit scores and reusable advantage.
- [ ] Self-improvement hook exists and does not invent telemetry.
- [ ] No gate is documentation-only.
- [ ] `tier: exemplary` fails closed if any gate is FAIL or Unknown.

Required deterministic check when files are present:

```bash
python scripts/validate_exemplary_skill.py <skill_folder>
```


## Build Execution Gates

Apply when building, rebuilding, packaging, exporting, or handling `/build_zip`. Load `references/build_execution_contract.md` AND `references/enforcement-gates.md`.

- [ ] All provided inputs were inspected before writing files.
- [ ] Correct artifact structure was inferred from request and source evidence.
- [ ] Complete files were generated or updated; no plan-only response.
- [ ] Required artifacts are present only when useful, not decorative.
- [ ] `MANIFEST.md`, `CHANGE_SUMMARY.md`, `VALIDATION.md`, `RUNBOOK.md`, or `README.md` were created only when justified by the artifact type.
- [ ] Deterministic validation or structural checks were run, or blocked checks are labeled.
- [ ] No invented credentials, secrets, contacts, licenses, domains, approvals, test results, or external facts.
- [ ] Zip bundle was created from approved generated/updated artifacts only.
- [ ] Final response includes validation results, Unknowns, zip manifest, and download link.
- [ ] **Gate artifacts (A–G) were produced at each workflow step before advancing.**
- [ ] **No gate was skipped without a documented blocker.**
- [ ] **Protocol violations (if any) are reported in the delivery response.**
- [ ] **build_feedback block is included in the delivery response.**

## Packaging Readiness

- [ ] Scope is bounded.
- [ ] Source intent is preserved.
- [ ] Complexity is justified.
- [ ] Required files are present.
- [ ] Validation gates pass.
- [ ] Archive root contains exactly one Skill folder.
- [ ] Final archive can be named `skill.zip`.
