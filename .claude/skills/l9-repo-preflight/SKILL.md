---
name: l9-repo-preflight
description: run a fail-OPEN repository preflight that never halts — probe the checkout, evaluate eight readiness gates, apply every safe reversible autofix (remove/gitignore generated artifacts, install declared tools, run the editable/npm install, ruff/eslint --fix, adapt a wrong blueprint to evidence), re-probe, and loop to a fixpoint, then emit ONLY the genuine blockers in machine-readable detail (class, severity, evidence, why-not-autofixable, remediation) for downstream. ecosystem-neutral (python + node/typescript, auto-detected). use when the user asks to preflight or verify a repo before work, auto-remediate/clean a checkout, get a machine-readable blocker list, gate implementation behind verified facts, or run the repository probe / decision tree.
skill_schema: 1
layer: control_plane
role: skill_entrypoint
tags: [l9, preflight, probe, fail-open, autofix, remediation, blockers, ecosystem-neutral, exemplary]
owner: igor_beylin
status: active
version: 2.0.0
updated: 2026-07-15
sources:
  - 10X Repository Preflight Decision Tree
  - 0_preflight_probe (read-only evidence probe)
---

# Repository Preflight — Fail-Open Remediation Engine (10X)

## Purpose

Get a checkout to a **known, buildable state without ever halting the run**. The engine probes the repo (read-only), walks **eight gates**, **applies every safe, reversible autofix** it can, re-probes, and loops to a fixpoint. Whatever cannot be safely auto-resolved is emitted as a **machine-readable genuine-blocker report** for a downstream human or agent — nothing is a dead end, and nothing safe is left un-fixed.

Two inversions from a classic gate:

- **Fail-OPEN.** The run always completes and always emits a report. No condition stops it. `blocker_count` and the report are the signal — not a halt.
- **Maximum autofix.** Every fix on a fixed **safe + reversible allow-list** is applied automatically. Only what is genuinely unsafe to auto-resolve survives as a blocker.

The governing stance is unchanged: **verified repository evidence outranks the blueprint.** A foreign expectation the repo does not meet is `adapt` (the plan is wrong — fix a copy of the plan), never a repo failure.

The primary deliverable answers, in machine-readable detail:

> "What did the preflight already fix, and what genuine blockers remain — each with its class, severity, evidence, why it could not be autofixed, and the exact remediation — for downstream?"

## Core Contract

| Mode | Output | Load |
|------|--------|------|
| remediate | The full fail-open loop: probe → autofix → re-probe → fixpoint → `blocker-report.json` + `autofix-log.json` | [references/preflight-pipeline.md](references/preflight-pipeline.md) + `scripts/remediate.py` |
| probe | Read-only evidence log (identity, worktree, toolchain, tests, CI, artifacts) | [scripts/preflight_probe.sh](scripts/preflight_probe.sh) + [references/probe-contract.md](references/probe-contract.md) |
| evaluate | Per-gate verdicts + autofix plans + genuine-blocker list from a probe log | [references/preflight-pipeline.md](references/preflight-pipeline.md) + `scripts/evaluate_preflight.py` |
| preflight | Alias for `remediate` | all references + [references/enforcement-gates.md](references/enforcement-gates.md) |

Default is **apply**. `--dry-run` emits the same report + autofix *plan* while mutating nothing. `--no-fix-code` leaves `ruff`/`eslint --fix` off the tracked source.

## The Eight Gates (autofix vs genuine blocker)

Every gate is evaluated every run (nothing halts). Each is `clear` · `autofixable` · `adapt` · `blocker`.

| # | Gate | AUTOFIX (safe, applied) | GENUINE BLOCKER (reported) |
|---|------|-------------------------|----------------------------|
| 1 | Probe completed | — | not a git repo / broken shell |
| 2 | Correct repo/branch/commit | wrong branch + clean tree + contract → `git switch` | wrong repo/commit; dirty-tree branch switch |
| 3 | Worktree clean | generated artifacts → gitignore + remove (deps kept) | **unknown-provenance files**; user tracked/staged edits |
| 4 | Required foundations | missing non-core + alt layout → adapt blueprint | missing **core** (wrong/partial checkout) |
| 5 | Toolchain matches | declared tool not installed → `pip install` / `npm ci`; contract≠repo → adapt | no ecosystem runtime at all |
| 6 | Install succeeded | repo pkg not importable → editable install; `node_modules` missing → `npm ci`; foreign pkg → adapt | install/build backend fails |
| 7 | Baseline reproduces | lint/format failures → `ruff`/`eslint --fix` (clean tree) | **new** type/test/logic failures |
| 8 | Ready | — | informational: `ready_after_remediation = (blockers == 0)` |

Full per-gate contract: [references/preflight-pipeline.md](references/preflight-pipeline.md).

## Fail-Open Guarantee & Blocker Criteria

The run **never halts**; instead it reports. A condition becomes a **genuine blocker** (severity-ranked, with evidence + remediation) precisely when it cannot be safely, reversibly auto-resolved:

1. **Unknown-provenance files** — unsafe to delete/ignore/commit automatically (offered: quarantine).
2. **User tracked/staged edits** — may be intended work; auto-reverting is destructive (offered: stash).
3. **New type/test/logic failures** — not mechanically fixable.
4. **Wrong repo/commit, or a missing core foundation** — no safe automatic resolution.
5. **A missing ecosystem runtime / broken build backend** — environment must be fixed.

Everything else is autofixed. These criteria replace the old "halt" red lines: the discipline is preserved (nothing unsafe is ever auto-applied), but it is expressed as *what gets reported*, not *what stops the run*.

## Authority Order

1. Security, safety, and legal constraints.
2. **Verified repository evidence** (probe output, resolved paths, tool versions, detected ecosystem).
3. The explicit expected contract / execution plan.
4. The blueprint's baked-in assumptions (package names, layout, language).
5. Convenience / speed.
6. `Unknown` — never fabricate; label and report as a blocker.

## Maximum Autofix (safe + reversible allow-list)

Applied by default; every action is recorded in `autofix-log.json` and is reversible:

`clean_generated` (remove throwaway artifacts + gitignore; dependency dirs like `node_modules` are kept, only ignored) · `git_switch_branch` (clean tree only) · `pip_install` / `npm_install` (honours repo pins/lockfiles) · `editable_install` · `ruff_fix` / `eslint_fix` (clean tree only — tool-owned, git-reversible) · `adapt_blueprint` (writes an evidence-adapted contract to a **new** file, never overwriting input).

**Never auto-applied:** deleting unknown files, reverting user tracked edits, editing code beyond mechanical format/lint, or anything off the allow-list. Those are the blockers above. `--dry-run` previews with zero mutation; `--no-fix-code` withholds source formatting.

## Ecosystem Neutrality (Python + Node/TypeScript)

The probe **auto-detects repo shape** — no language or package name is baked in. It emits `PROBE_ECOSYSTEM` (node/python/…), auto-discovers source dirs, Python packages (empty on non-Python repos, so a TS/Node repo is never probed for a Python import), and foundations (`package.json` vs `pyproject.toml`, …). Gates 5/6/7 and the autofix actions dispatch per ecosystem: Python → ruff/mypy/pytest, `pip`, editable install; Node → eslint/tsc/prettier, `npm ci`, `node_modules`. A tool or runtime that is not present is skipped, never a false failure. Override any token via `PROBE_PACKAGES` / `PROBE_FOUNDATIONS` / `PROBE_ECOSYSTEM`.

## Compact Workflow

Each step deposits a gate artifact (see [references/enforcement-gates.md](references/enforcement-gates.md)).

1. **Run the probe** — `scripts/preflight_probe.sh` → a read-only evidence log. → **Gate A.**
2. **Extract expertise** — run `extract_expertise` over the probe evidence + decision tree to build the intelligence model; then `compress_expertise` into the smallest behavior-changing form. → **Gate B** (exemplary builds).
3. **Evaluate gates 1–8** — `evaluate_preflight.py` → per-gate verdict + autofix plans + genuine blockers. → **Gate C.**
4. **Apply safe autofixes** — `remediate.py` dispatches the allow-list; every action logged, reversible. → **Gate D.**
5. **Re-probe** — every fix re-enters at step 1 from fresh evidence. → **Gate E/F.**
6. **Loop to a fixpoint** — until no new autofix applies (or `--max-iters`). → **Gate F.**
7. **Emit the blocker report** — `blocker-report.json` (genuine blockers only) + `autofix-log.json`; the run always completes. → **Gate G.**

### Mandatory Exemplary Pipeline

```text
run_probe → extract_expertise → compress_expertise → evaluate_gates → apply_autofixes → run_exemplary_gate → emit_blocker_report
```

`extract_expertise` is required for any claimed exemplary tier; fail closed when the model is missing. The `exemplary_gate` is the deterministic check `scripts/validate_exemplary_skill.py`.

## Resource Map

- [references/preflight-pipeline.md](references/preflight-pipeline.md) — the eight-gate contract: per-gate autofix action vs blocker criteria, the fail-open loop, and the verdict vocabulary.
- [references/probe-contract.md](references/probe-contract.md) — probe section → gate map; the auto-detected, ecosystem-neutral parameterizable surface (`PROBE_ECOSYSTEM`/`PROBE_PACKAGES`/`PROBE_FOUNDATIONS`).
- [references/enforcement-gates.md](references/enforcement-gates.md) — **runtime enforcement layer**: completeness + audit (every gate ran; every autofix logged + reversible; every blocker carries evidence + remediation); protocol-violation detection.
- [scripts/preflight_probe.sh](scripts/preflight_probe.sh) — the read-only evidence probe (auto-detects ecosystem; writes only a log). `bash scripts/preflight_probe.sh`.
- [scripts/evaluate_preflight.py](scripts/evaluate_preflight.py) — deterministic classifier: probe log (+ optional contract/baseline) → verdicts + autofix plans + genuine blockers. `python3 scripts/evaluate_preflight.py <log> [--expected C] [--baseline B]`.
- [scripts/remediate.py](scripts/remediate.py) — the fail-open loop + safe autofix executor. `python3 scripts/remediate.py [REPO] [--expected C] [--dry-run] [--no-fix-code]`.
- [schemas/blocker-report.schema.json](schemas/blocker-report.schema.json) — the machine-readable deliverable (genuine blockers + autofix summary); example in [schemas/blocker-report.example.json](schemas/blocker-report.example.json).
- [schemas/autofix-log.schema.json](schemas/autofix-log.schema.json) — the reversible-action audit trail.
- [schemas/preflight-report.schema.json](schemas/preflight-report.schema.json) — the evaluate-mode report (per-gate verdicts + autofix plans); example in [schemas/preflight-report.example.json](schemas/preflight-report.example.json).
- [schemas/expected-contract.schema.json](schemas/expected-contract.schema.json) — the optional blueprint that verified evidence overrides.
- [scripts/validate_exemplary_skill.py](scripts/validate_exemplary_skill.py) — deterministic exemplary-tier gate for this skill itself.

## Exemplary Provenance & Self-Improvement

Compiled through the L9 exemplary pipeline: `parse_source → extract_expertise → compress_expertise → design_skill → run_exemplary_gate → package`. The compressed intelligence layer ships as auditable artifacts:

- [expertise_model.yaml](expertise_model.yaml) — experts, doctrine, invariants, authority hierarchy, activation/reject signals, adapters, failure modes, leverage points (the `extract_expertise` / `compress_expertise` output).
- [skill_intelligence_report.yaml](skill_intelligence_report.yaml) — activation model (measured specificity + false-positive-risk), authority model, expert heuristics, evidence hierarchy, `exemplary_gate` results, tier decision.
- Deterministic gate: [scripts/validate_exemplary_skill.py](scripts/validate_exemplary_skill.py) — `tier: exemplary` is claimed only because this validator passes. Reference to `enforcement-gates` is mandatory and lives in [references/enforcement-gates.md](references/enforcement-gates.md).

**After-use improvement hook** — capture ONLY when the user reports a bad result or asks to iterate: `false_blocker` (reported a blocker that was autofixable), `unsafe_autofix` (auto-applied something that wasn't safe/reversible), `missed_ecosystem` (a language not detected), or `non_termination` (loop didn't converge). Feed captures back into the autofix allow-list in `scripts/remediate.py` and the gate logic in `scripts/evaluate_preflight.py`.

## Validation

Before delivery: the run completes and emits `blocker-report.json` + `autofix-log.json`; every applied autofix is on the allow-list and reversible; every genuine blocker carries class, severity, evidence, why-not-autofixable, and remediation; the reports validate against their schemas; and for this skill itself, `scripts/validate_exemplary_skill.py` passes.

## Failure Handling

Nothing halts. If an autofix cannot be applied it is recorded as skipped and the underlying condition is reported as a blocker with remediation; if the probe cannot run at all, exit 2 with the reason. Never fabricate identity, a clean tree, or a passing baseline; never present a repo with genuine blockers as ready. The report — not a stop — is always the deliverable.
