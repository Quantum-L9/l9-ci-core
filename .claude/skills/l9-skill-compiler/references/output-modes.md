<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: output_contract
tags: [skill, outputs, modes, response_contract]
owner: igor_beylin
status: active
version: 2.0.0
updated: 2026-06-15
/L9_META -->

# Output Modes

## Purpose

Use this file to select the right response shape for the user's request.

## Mode Selection

| User intent | Mode |
|---|---|
| asks whether a Skill is worth building | discuss |
| asks for architecture before files | design |
| asks to review an existing Skill | analyze |
| asks to create full contents | build |
| asks to improve or replace an existing Skill | rebuild |
| asks for smarter, exemplary, 10x, low-drift, high-autonomy, or domain-expert skill behavior | smart/exemplary |
| asks for distributable archive, zip, download link, or `/build_zip` | package/build_zip |

## Discuss Mode

Return:

- direct recommendation
- tradeoffs
- next action

Keep it short. Do not produce file contents unless requested.

## Design Mode

Return:

- objective
- skill concept
- trigger description
- file plan
- resource map
- validation plan

Do not create files unless requested.

## Analyze Mode

Return:

- strengths
- gaps
- duplication
- trigger quality
- structure risks
- resource misuse
- recommended edits

Prioritize edits by functional impact.

## Build Mode

Load `references/build_execution_contract.md` and `references/enforcement-gates.md`. Return:

- complete folder tree
- complete file contents or generated archive
- validation checklist with status
- change summary when modifying an existing pack
- zip link when packaging is requested or useful
- **gate artifacts** (A through G as applicable) proving each step was executed
- **protocol_violations** list (empty if compliant)
- **build_feedback** block

Do not return partial files or a plan-only response when full build is requested.

## Rebuild Mode

Return:

- old versus new gap analysis
- revised structure
- revised file contents or archive
- migration notes
- validation checklist

Preserve source intent unless the user explicitly changes it.

## Smart / Exemplary Mode

Return or internally use:

- quality classification: basic_skill, strong_skill, or exemplary_skill
- activation model
- authority order
- expert heuristics
- conditional adapter map
- failure modes and prevention rules
- drift controls
- self-improvement hook
- exact file changes needed

Do not reward complexity. If the intelligence layer is weak, say so and build a strong skill rather than faking exemplary status.

## Package / Build Zip Mode

Load `references/build_execution_contract.md` and `references/enforcement-gates.md`. Return:

- `skill.zip` or requested zip bundle
- manifest or zip file list
- validation status with checks actually run
- Unknowns and blocked checks
- download link
- **Gate G artifact** (package_record) proving zip matches file_tree
- **protocol_violations** list (empty if compliant)
- **build_feedback** block

Package only after validation passes or blocked checks are explicitly labeled. Never claim fake validation.

## Next Prompt Discipline

When a next prompt would reduce turns or preserve momentum, provide exactly one highest-leverage next prompt or next action. Do not provide a menu unless the user asks for options.

## Formatting Rules

- Prefer compact sections.
- Prefer YAML for specs and validation status.
- Label assumptions and unknowns.
- Do not include commentary inside artifact files unless it is part of the file contract.
- Keep final responses decision-ready.
