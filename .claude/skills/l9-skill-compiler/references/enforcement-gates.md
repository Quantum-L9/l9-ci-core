<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: enforcement_gates
tags: [skill, compiler, enforcement, validation, artifacts, protocol-violation]
owner: igor_beylin
status: active
version: 3.2.0
updated: 2026-06-18
/L9_META -->

# Enforcement Gates

## Purpose

Prevent the compiler from skipping its own workflow steps by requiring concrete artifacts at each stage. Rules without enforcement are suggestions. This file defines the **proof-of-compliance** checkpoints the compiler MUST produce before advancing to the next workflow step.

Without this layer, the compiler can:
- Claim it "extracted expertise" without producing the model
- Skip local validation and ship broken packs
- Batch-ignore its own checklist items
- Produce a zip without running structural checks

This file makes those failures structurally impossible.

## Gate Architecture

```text
Step 1 ──→ [GATE A] ──→ Step 2-3 ──→ [GATE B] ──→ Step 4-6 ──→ [GATE C]
──→ Step 7 ──→ [GATE D] ──→ Step 8-9 ──→ [GATE E] ──→ Step 10-11 ──→ [GATE F]
──→ Step 12 ──→ [GATE G]
```

Each gate requires a specific artifact. If the artifact is missing or invalid, the compiler MUST NOT proceed.

## Gate A: Source Parsed

**After Step 1 (Parse source)**

Required artifact:
```yaml
source_parse:
  objective: "{what the user wants built}"
  scope: "{bounded description of what's in/out}"
  triggers: ["{activation phrase 1}", "{activation phrase 2}"]
  workflow_steps: ["{step 1}", "{step 2}"]
  constraints: ["{constraint 1}"]
  outputs: ["{output 1}"]
  resources_provided: ["{file or content 1}"]
  risks: ["{risk 1}"]
  unknowns: ["{unknown 1}" | "none"]
```

Validation:
- [ ] `objective` is non-empty and specific
- [ ] `scope` has clear boundaries (not "everything")
- [ ] At least 1 trigger phrase identified
- [ ] At least 1 workflow step extracted
- [ ] Unknowns are explicitly listed (even if "none")

**STOP if:** Source material is empty or unintelligible → ask user for clarification.

## Gate B: Expertise Extracted (Exemplary/Smart mode only)

**After Steps 2-3 (Extract + Compress expertise)**

Required artifact:
```yaml
expertise_model:
  experts: ["{role 1}", "{role 2}"]
  doctrine: ["{behavior-changing rule 1}"]
  invariants: ["{must-remain-true rule 1}"]
  authority_hierarchy: ["{priority 1} > {priority 2}"]
  activation_signals: ["{signal 1}"]
  reject_signals: ["{signal 1}"]
  adapters: ["{condition → behavior change}"]
  failure_modes: ["{mode 1}: {prevention}"]
  leverage_points: ["{point 1}"]
```

Validation:
- [ ] `doctrine` contains behavior-changing rules (not summaries)
- [ ] `invariants` are testable assertions
- [ ] `activation_signals` ≤ 5 (hard cap)
- [ ] `reject_signals` ≤ 5 (hard cap)
- [ ] `adapters` ≤ 3 (hard cap)
- [ ] Every entry is a compressed judgment, not a description

**STOP if:** Cannot extract meaningful expertise → classify as `strong_skill`, not `exemplary`.

## Gate C: File Tree Designed

**After Steps 4-6 (Design file tree + resource map)**

Required artifact:
```yaml
file_tree:
  total_files: {integer}
  structure:
    - path: "SKILL.md"
      role: "control_plane"
      purpose: "{one line}"
    - path: "references/{name}.md"
      role: "{role}"
      purpose: "{one line}"
  resource_map_complete: true
  every_file_has_purpose: true
  no_decorative_files: true
```

Validation:
- [ ] `SKILL.md` is in the tree
- [ ] Every file has a distinct `purpose` (no duplicates)
- [ ] No file exists "because a template generated it"
- [ ] Total files is reasonable for scope (not bloated)
- [ ] Every reference file is linked from SKILL.md resource map

**STOP if:** File tree has duplicate responsibilities → merge files before building.

## Gate D: Files Built (Pre-Validation)

**After Step 7 (Build complete files)**

Required artifact:
```yaml
build_manifest:
  files_written: {integer}
  files_complete: {integer}  # must equal files_written
  stubs_remaining: 0
  todos_remaining: 0
  placeholder_content: 0
  every_file_non_empty: true
  total_lines: {integer}
```

Validation:
- [ ] `stubs_remaining == 0` (zero-stub gate)
- [ ] `todos_remaining == 0` (no unfinished markers)
- [ ] `placeholder_content == 0` (no fake content)
- [ ] `files_complete == files_written` (everything finished)
- [ ] Every file was actually written to disk (not just planned)

**STOP if:** Any stub, TODO, or placeholder remains → complete it before proceeding.

## Gate E: Validation Passed

**After Steps 8-9 (Run exemplary gate + validation checklist)**

Required artifact:
```yaml
validation_report:
  checklist_items_total: {integer}
  checklist_items_passed: {integer}
  checklist_items_failed: {integer}
  checklist_items_na: {integer}
  zero_stub_gates: pass | fail
  metadata_gates: pass | fail
  structure_gates: pass | fail
  exemplary_gates: pass | fail | not_applicable
  deterministic_scripts_run: ["{script}: {result}"]
  tier_classification: "exemplary" | "strong" | "developing" | "failed"
  honest_assessment: true
```

Validation:
- [ ] `checklist_items_failed == 0` for release
- [ ] `zero_stub_gates == pass`
- [ ] `honest_assessment == true` (no fake pass claims)
- [ ] If `exemplary_gates == fail` → tier MUST NOT be "exemplary"
- [ ] Deterministic scripts actually ran (not "would pass")

**STOP if:** Any critical gate fails → fix before packaging. Do NOT ship with known failures.

## Gate F: Repo Wiring Complete (when applicable)

**After Steps 10-11 (Wire into repo registries)**

Required artifact:
```yaml
wiring_report:
  skill_name: "{name}"
  skill_path: "{path}"
  readme_updated: true | false | not_applicable
  agents_md_updated: true | false | not_applicable
  subagent_preload: true | false | "documented_skip"
  l9_wire_skill_executed: true | false | not_applicable
```

Validation:
- [ ] If in a PlasticOS repo → `l9_wire_skill_executed == true`
- [ ] If `readme_updated == false` → reason documented
- [ ] Subagent preload decision is explicit (not silently skipped)

**STOP if:** Wiring required but blocked → document blocker, continue to packaging with note.

## Gate G: Package Ready

**After Step 12 (Deliver)**

Required artifact:
```yaml
package_record:
  zip_created: true
  zip_path: "{path}"
  zip_manifest: ["{file 1}", "{file 2}"]
  total_files_in_zip: {integer}
  total_lines_in_zip: {integer}
  validation_status: "all_passed" | "passed_with_na" | "blocked"
  unknowns: ["{unknown 1}" | "none"]
  download_link_provided: true
```

Validation:
- [ ] `zip_created == true`
- [ ] `zip_manifest` matches `file_tree` from Gate C
- [ ] `validation_status != "blocked"` (or blocker is documented)
- [ ] `download_link_provided == true`

**STOP if:** Zip cannot be created → return files inline with blocker explanation.

## Protocol Violation Detection

If at ANY point the compiler:
- Advances past a gate without producing its artifact → **VIOLATION: gate-skip**
- Claims `tier: exemplary` without Gate B artifact → **VIOLATION: fake-exemplary**
- Ships a zip with `stubs_remaining > 0` → **VIOLATION: stub-in-package**
- Claims "validation passed" without running checks → **VIOLATION: fake-validation**
- Produces a file tree with duplicate responsibilities → **VIOLATION: bloat**
- Skips expertise extraction for smart/exemplary request → **VIOLATION: expertise-skip**

Violations MUST be:
1. Logged in the delivery response under `protocol_violations`
2. Reported to the user
3. Used to improve future builds (feedback loop)

## Feedback Loop

After every build, record:
```yaml
build_feedback:
  gates_passed_first_attempt: {integer}/{total}
  gates_required_rework: ["{gate}: {reason}"]
  protocol_violations: ["{violation}" | "none"]
  time_spent_on_rework: "low" | "medium" | "high"
  lesson_learned: "{one sentence improvement for next build}"
```

This feedback is NOT stored persistently but MUST be included in the delivery response to enable recursive learning across sessions.

## Enforcement Mechanism

The compiler MUST produce each gate artifact **in its working notes or response** before advancing. The artifacts serve as:
1. **Proof of compliance** — auditable trail that the protocol was followed
2. **Self-check** — producing the artifact forces the compiler to actually do the work
3. **Quality signal** — if an artifact can't be produced honestly, the build has a real problem

If an artifact cannot be produced, the compiler is stuck at that gate and MUST either:
- Fix the issue preventing artifact production
- Ask the user for missing information
- Emit a `blocked` status with the exact blocker
- NEVER fabricate an artifact to pass a gate
