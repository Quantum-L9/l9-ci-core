<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
role: smart_exemplary_contract
tags: [skill, intelligence_extraction, exemplary, compression, drift_control]
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# SMART Exemplary Skill Contract

## Purpose

Use this contract when the user wants a skill that is not merely valid, complete, or well organized, but meaningfully smarter in use.

The goal is behavioral leverage: extract the smallest set of domain signals, expert heuristics, authority rules, adapters, and failure controls that make the future model act like a capable operator.

Do not increase quality by adding bulk. Increase quality by improving judgment.

## Core Law

A skill is exemplary only when it changes how ChatGPT decides:

- when to activate
- what to ignore
- which source wins during conflict
- which domain law applies
- what action creates leverage
- when to stop, fail closed, or ask
- how to learn from bad runs

If a rule does not change a future decision or output, exclude it.

## SMART Meaning

```yaml
smart:
  selective: "fires on strong signals and rejects near-misses"
  minimal: "keeps only rules that change behavior"
  adaptive: "uses conditional adapters instead of global doctrine"
  ranked: "prioritizes by authority, risk, and leverage"
  testable: "quality can be checked without trusting vibes"
```

## Intelligence Extraction Layer

Run this layer between source parsing and skill design.

```yaml
skill_intelligence_extraction:
  objective: "extract the smallest set of rules that make the skill behave like a domain expert"
  hard_limits:
    max_strong_activation_signals: 5
    max_weak_activation_signals: 5
    max_reject_signals: 5
    max_authority_rules: 5
    max_expert_heuristics: 7
    max_adapters_default: 3
    max_failure_modes: 5
    max_self_improvement_prompts: 4
  reject:
    - generic_best_practices
    - obvious_checklists
    - rules_that_restate_the_task
    - adapters_without_different_decision_rules
    - files_that_do_not_change_behavior
    - broad_doctrine_as_default_law
    - unbounded_reference_sprawl
  output:
    - activation_model
    - authority_order
    - expert_heuristics
    - adapter_map
    - failure_modes
    - drift_controls
    - self_improvement_hook
```

## Required Intelligence Primitives

### 1. Activation Model

Every generated skill must define:

```yaml
activation_model:
  strong_signals: []
  weak_signals: []
  reject_signals: []
  false_positive_risks: []
```

Rules:

- Strong signals should be concrete terms, file patterns, task phrases, or artifact features.
- Weak signals may support activation but must not trigger alone when ambiguity is high.
- Reject signals are mandatory. A skill without reject signals is too eager.
- If false positives cannot be identified, mark the skill as strong, not exemplary.

### 2. Authority Order

Every generated skill must include a conflict-resolution chain.

Default:

```yaml
authority_order:
  - latest_user_instruction
  - uploaded_or_connected_source_artifact
  - contracts_and_invariants
  - adrs_and_architecture
  - implementation_or_runtime_evidence
  - comments_and_docs
  - model_inference
```

Rule: higher authority wins. Inference never overrules source evidence.

### 3. Expert Heuristics

Extract conditional expert moves, not generic tasks.

Format:

```yaml
expert_heuristic:
  condition: "observable trigger"
  judgment: "expert interpretation"
  action: "required model behavior"
```

Good:

```yaml
condition: "a change touches security groups or record rules"
judgment: "security posture may change even when code diff is small"
action: "require explicit risk review and validation path before recommending merge"
```

Bad:

```yaml
action: "check security"
```

### 4. Adapter Map

Create adapters only when the domain changes the decision rules.

```yaml
adapter_map:
  core_default: "domain-agnostic behavior"
  adapters:
    - name: string
      load_when: []
      changes:
        - authority_order
        - validation_gates
        - output_shape
        - forbidden_moves
```

Rules:

- Do not make any domain doctrine global unless the whole skill is domain-specific.
- Cap default adapters at three unless the user explicitly asks for a larger pack.
- An adapter must say what it changes. If it changes nothing, delete it.

### 5. First-Order Review Loop

Add one compact loop that improves judgment without creating ritual theater.

```yaml
first_order_review_loop:
  - restate_real_objective
  - identify_smallest_high_leverage_behavior
  - test_against_failure_modes
  - remove_non_behavioral_complexity
  - produce_or_update_skill
```

### 6. Failure Modes

Every generated skill must identify how it can go wrong.

```yaml
failure_modes:
  - false_activation
  - under_activation
  - doctrine_overreach
  - generic_output
  - unsupported_capability_claim
```

Each failure mode must have a prevention rule.

### 7. Self-Improvement Hook

Every generated skill should include a compact after-use capture block.

```yaml
after_use_capture:
  - missed_trigger
  - false_trigger
  - recurring_user_correction
  - output_that_required_manual_rework
```

Rule: capture observations only when the user reports a bad run or requests iteration. Do not invent telemetry.

## Exemplary Gate

A skill passes the exemplary gate only if:

```yaml
exemplary_gate:
  activation_has_reject_signals: true
  authority_order_present: true
  expert_heuristics_present: true
  adapters_are_conditional: true
  failure_modes_have_prevention_rules: true
  self_improvement_hook_present: true
  no_reference_without_trigger: true
  no_checklist_item_without_behavior_change: true
  no_global_domain_doctrine_unless_domain_specific: true
```

If any gate fails, classify the result as `strong_skill`, not `exemplary_skill`.

## Compression Test

Before adding any rule, file, adapter, checklist, or script, ask:

```yaml
compression_test:
  changes_future_decision: true_or_false
  reduces_false_positive_or_drift: true_or_false
  increases_reuse_without_bloat: true_or_false
  can_be_triggered_conditionally: true_or_false
```

Keep it only if at least two answers are true and no answer introduces unsupported scope.

## Output Requirement For Compiler

When building or rebuilding a skill, produce an internal `smart_exemplary_spec` before writing files. Use `references/canonical-smart-exemplary-spec.yaml` as the template.

The spec may stay as a reference artifact or be summarized in the final response. Do not dump long specs into `SKILL.md`.


## Required Compiler Outputs

Every smart or exemplary compiled skill must produce or internally construct:

```yaml
expertise_model.yaml:
  experts: []
  doctrine: []
  invariants: []
  authority_hierarchy: []
  activation_signals: []
  reject_signals: []
  adapters: []
  failure_modes: []
  leverage_points: []

skill_intelligence_report.yaml:
  activation_model: {}
  authority_model: {}
  expert_heuristics: []
  doctrine: []
  invariants: []
  adapter_map: {}
  failure_modes: []
  leverage_points: []
  evidence_hierarchy: {}
  exemplary_gate_results: {}
  tier_decision: strong | developing | failed | exemplary
```

## Tier Award Policy

`tier: exemplary` is allowed only when all required gates pass with evidence. If any gate is FAIL, Unknown, or documentation-only, the maximum tier is `strong`.

Required gates:

- activation_precision
- adapter_architecture
- evidence_hierarchy
- doctrine_extraction
- expert_heuristics
- failure_modes
- leverage_model
- self_improvement_hook
- compiler_enforcement_gates
- skill_intelligence_report

## Deterministic Enforcement

Use `scripts/validate_exemplary_skill.py <skill_folder>` when file artifacts exist. Use an equivalent gate report only when validating internal/emitted artifacts in chat.
