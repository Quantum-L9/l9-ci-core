---
name: l9-repo-preflight
description: execute a complete fail-closed repository preflight pipeline before any implementation — run a read-only evidence probe, then evaluate eight sequential readiness gates (probe completed, correct repo/branch/commit, clean worktree, required foundations present, toolchain matches the execution contract, install succeeded, baseline validation reproduces, implementation ready), classify every failure against a fixed taxonomy, loop fix -> re-run probe -> verify -> continue, and emit a readiness verdict plus the single smallest next action. use when the user asks to preflight, verify a repo before starting work, run the repository probe / decision tree, check worktree/baseline readiness, confirm a checkout is safe to build on, or gate implementation behind verified repository facts.
skill_schema: 1
layer: control_plane
role: skill_entrypoint
tags: [l9, preflight, probe, readiness-gate, git-hygiene, baseline, fail-closed, exemplary]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-15
sources:
  - 10X Repository Preflight Decision Tree
  - 0_preflight_probe (read-only evidence probe)
---

# Repository Preflight Pipeline (10X)

## Purpose

Prove a checkout is **safe to build on before a single line is changed**. The pipeline gathers repository evidence with a read-only **probe**, then walks **eight sequential gates**; each gate either passes or routes into a bounded failure taxonomy whose only exit is `Fix → Re-run Probe → Verify → Continue`. Nothing advances on assumption. The output is a machine-readable **readiness verdict** and the **single smallest next action**.

The governing stance is inverted from the usual: **verified repository evidence outranks the blueprint.** When the plan and the repo disagree, the plan is wrong — you adapt the blueprint to the evidence, never the repo to the blueprint. This is what keeps preflight honest on a repo it has never seen.

The pipeline answers, with trace evidence:

> "Did the probe run, am I on the right repo/branch/commit, is the worktree clean, are the required foundations here, does the toolchain match, did install succeed, does the baseline reproduce — and if not, what is the one smallest thing to fix before I re-run?"

## Core Contract

| Mode | Output | Load |
|------|--------|------|
| probe | Read-only evidence log (identity, worktree, toolchain, tests, CI, artifacts) | [scripts/preflight_probe.sh](scripts/preflight_probe.sh) + [references/probe-contract.md](references/probe-contract.md) |
| evaluate | Per-gate verdicts + red-line check + readiness report from a probe log | [references/preflight-pipeline.md](references/preflight-pipeline.md) + `scripts/evaluate_preflight.py` |
| remediate | The single smallest next action for the first blocking gate (autofix-safe only) | [references/preflight-pipeline.md](references/preflight-pipeline.md) |
| preflight | The full loop: probe → evaluate → remediate → re-run until ready or blocked | all references + [references/enforcement-gates.md](references/enforcement-gates.md) |

## The Eight Gates

Sequential and fail-closed: a gate is evaluated only after the prior gate passes. Every **NO** loops back through the probe.

| # | Gate | PASS condition | Failure taxonomy |
|---|------|----------------|------------------|
| 1 | Probe completed | log has every section marker + `PROBE COMPLETE`, no fatal error | shell error · git error · missing command |
| 2 | Correct repo / branch / commit | identity matches the expected contract (or is human-confirmed) | wrong repo · wrong branch · wrong commit |
| 3 | Worktree clean | no tracked-modified, staged, or **unknown** untracked files | tracked · generated · **unknown** |
| 4 | Required foundations present | expected foundations resolve to real paths | missing-but-expected (wrong checkout) · missing-and-not-expected (**adapt blueprint**) |
| 5 | Toolchain matches contract | python / package-manager / test-tools satisfy the contract | python · package mgr · test tools (repo-defined tooling wins) |
| 6 | Installation succeeded | declared install method exits 0, packages import | dependency · build backend · editable install (repo vs environment) |
| 7 | Baseline validation reproduces | pytest / mypy / ruff / schema run; failures are **existing**, not new | existing (record) vs **new** (stop) |
| 8 | Implementation ready | gates 1–7 pass and no red line tripped | else: smallest blocker first |

Full per-gate contract, remediation, and loop-back: [references/preflight-pipeline.md](references/preflight-pipeline.md).

## Golden Rules (red lines)

The readiness verdict is **NOT READY** if any of these hold — enforce them as hard gates, never autofix past them:

1. **Never continue with unknown files.** Untracked files of unknown provenance → stop until identified.
2. **Never continue if the baseline cannot be reproduced.** A build/test baseline that will not reproduce is a stop.
3. **Never modify code until repository facts are verified.** No mutation before gates 1–3 pass.
4. **Never adapt the repository to the blueprint.** Adapt the blueprint to verified repository evidence.
5. **Every NO loops back** through `Fix → Re-run Probe → Verify → Continue` — a gate is never waved through.

## Authority Order

When the blueprint, assumptions, and evidence conflict, apply this cascade:

1. Security, safety, and legal constraints.
2. **Verified repository evidence** (probe output, resolved paths, tool versions).
3. The explicit expected contract / execution plan.
4. The uploaded blueprint's baked-in assumptions (package names, layout).
5. Convenience / speed.
6. `Unknown` — **fail closed**: never fabricate identity, ownership, or a passing baseline; label `Unknown` and stop.

## Defaults & Autofix (ON by default)

Rather than stall on a fixable NO, **safe non-code remediation is auto-applied and recorded**: update `.gitignore` for a known-generated artifact, remove untracked **known-generated** files (`.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.egg-info`), install a missing pinned tool, recreate a broken venv. Autofix **never** touches an unknown file, **never** edits tracked code, and **never** manufactures a baseline — those are the red lines above. Run `scripts/evaluate_preflight.py --strict` for a hard, fail-closed audit that disables autofix and treats every NO as a stop.

## Compact Workflow

Each step produces a gate artifact (see [references/enforcement-gates.md](references/enforcement-gates.md)); do not advance without it.

1. **Run the probe** — `scripts/preflight_probe.sh` → a timestamped read-only evidence log. → **Gate A.**
2. **Extract expertise** — run `extract_expertise` over the probe evidence + the decision tree to build the intelligence model (experts, doctrine, invariants, activation/reject signals, adapters, failure modes, leverage points); then `compress_expertise` into the smallest behavior-changing form. → **Gate B** (exemplary builds).
3. **Evaluate gates 1–8** — `python3 scripts/evaluate_preflight.py <log> [--expected <contract>]`: each gate → `pass` / `blocked` / `confirm` / `adapt`, plus the red-line check. → **Gate C.**
4. **Classify failures** — map each NO to its taxonomy; separate **existing** baseline failures (record) from **new** (stop). Unknown files and new failures are red lines. → **Gate D.**
5. **Remediate the smallest blocker** — apply the single safe next action (or stop and report for a hard-stop). → **Gate E.**
6. **Re-run the probe** — every fix re-enters at step 1; a gate is never verified from stale evidence. → **Gate F.**
7. **Emit readiness** — the readiness report + verdict + the one next action; `ready` only when gates 1–7 pass and no red line tripped. → **Gate G.**

### Mandatory Exemplary Pipeline

```text
run_probe → extract_expertise → compress_expertise → evaluate_gates → classify_failures → run_exemplary_gate → emit_readiness
```

`extract_expertise` is required for any claimed exemplary tier; fail closed when the model is missing or incomplete. The `exemplary_gate` is the deterministic check `scripts/validate_exemplary_skill.py`.

## Resource Map

- [references/preflight-pipeline.md](references/preflight-pipeline.md) — the eight-gate contract: each gate's question, PASS condition, failure taxonomy, remediation, and the `Fix → Re-run → Verify → Continue` loop.
- [references/probe-contract.md](references/probe-contract.md) — what the probe emits section-by-section, which gate each section feeds, and the parameterizable surface (expected repo/branch/commit, foundations, packages, toolchain).
- [references/enforcement-gates.md](references/enforcement-gates.md) — **runtime enforcement layer**: the required proof-of-compliance artifact at each of the eight gates; protocol-violation detection (advancing past a NO without a re-run).
- [scripts/preflight_probe.sh](scripts/preflight_probe.sh) — the read-only evidence probe. Foreign, repo-specific tokens are lifted into an overridable config block; nothing is written except the log. `bash scripts/preflight_probe.sh`.
- [scripts/evaluate_preflight.py](scripts/evaluate_preflight.py) — deterministic gate evaluator: parse a probe log (+ optional expected contract), emit per-gate verdicts, the red-line check, the readiness report, and the single next action. `python3 scripts/evaluate_preflight.py <log> [--expected <contract>] [--strict]`.
- [schemas/preflight-report.schema.json](schemas/preflight-report.schema.json) — the readiness-report contract (Draft 2020-12); worked example in [schemas/preflight-report.example.json](schemas/preflight-report.example.json).
- [schemas/expected-contract.schema.json](schemas/expected-contract.schema.json) — the optional expected-repo blueprint (identity, foundations, packages, toolchain) that verified evidence overrides.
- [scripts/validate_exemplary_skill.py](scripts/validate_exemplary_skill.py) — deterministic exemplary-tier gate for this skill itself.

## Exemplary Provenance & Self-Improvement

Compiled through the L9 exemplary pipeline: `parse_source → extract_expertise → compress_expertise → design_skill → run_exemplary_gate → package`. The compressed intelligence layer ships as auditable artifacts, not prose:

- [expertise_model.yaml](expertise_model.yaml) — experts, doctrine, invariants, authority hierarchy, activation/reject signals, adapters, failure modes, leverage points (the `extract_expertise` / `compress_expertise` output).
- [skill_intelligence_report.yaml](skill_intelligence_report.yaml) — activation model (measured specificity + false-positive-risk), authority model, expert heuristics, evidence hierarchy, `exemplary_gate` results, tier decision.
- Deterministic gate: [scripts/validate_exemplary_skill.py](scripts/validate_exemplary_skill.py) — `tier: exemplary` is claimed only because this validator passes. Reference to `enforcement-gates` is mandatory and lives in [references/enforcement-gates.md](references/enforcement-gates.md).

**After-use improvement hook** — capture ONLY when the user reports a bad verdict or asks to iterate: `false_ready` (declared ready but wasn't), `false_block` (blocked on evidence that was actually fine), `missed_unknown_file`, or `misclassified_baseline` (called a new failure existing or vice-versa). Feed captures back into the gate logic in `scripts/evaluate_preflight.py` and the taxonomy in `references/preflight-pipeline.md`.

## Validation

Before declaring a verdict: the probe log is complete (all sections + `PROBE COMPLETE`); every gate carries evidence or a labeled `Unknown`; no red line is tripped for a `ready` verdict; the readiness report validates against `schemas/preflight-report.schema.json`; and for this skill itself, `scripts/validate_exemplary_skill.py` passes.

## Failure Handling

State the exact blocking gate and its taxonomy class; label missing/unverifiable facts `Unknown`; never fabricate identity, a clean worktree, or a passing baseline; provide the smallest safe next action; if a red line cannot be satisfied, deliver the verdict as `blocked` with the specific red line and remediation target — never present a red-lined checkout as `ready`.
