<!-- L9_META
l9_schema: 1
parent: l9-devpack-compiler
layer: reference
role: quality_gates
tags: [dpk, scoring, readiness, red-lines, handoff]
owner: igor_beylin
status: active
version: 1.1.0
updated: 2026-07-15
/L9_META -->

# Quality Gates & Readiness Scoring

## Purpose

Score a compiled dev pack 0–100 for handoff readiness, and enforce the red-line overrides that instantly disqualify it. Implemented deterministically by `scripts/validate_devpack.py`.

## Scoring Matrix (weights)

| Category | Weight | Strict compliance metric |
|---|---|---|
| Repository Clarity | 10% | verification tokens present in root structural docs |
| Architecture Mapping | 15% | 100% alignment between code imports and `.ai/repository-map.yaml` |
| Local Reproducibility | 10% | single-command deterministic setup (`scripts/bootstrap`) |
| Test & Eval Coverage | 15% | deterministic tests pass + eval delta metrics for AI features |
| Security Boundaries | 10% | credential-leak testing + access-path authorization checks |
| Observability Integrity | 15% | every alert statically correlates to a resolving runbook |
| Deployment & Rollback | 10% | machine-readable inverse-patch / rollback verified via dry-run |
| Transition Clarity | 15% | signed debt ledger with valid remediation targets |

## Readiness Bands

```text
[90–100]  Independently Operable / Safe Handoff
[80–89]   Conditionally Clear (minor document drift)
[0–79]    Handoff Rejected (execution blocked)
```

## 🚨 Red-Line Overrides (score → 0 instantly)

Any one of these zeroes the cumulative score regardless of other categories:

1. **No production operations owner** in `.ai/manifest.yaml` (`ownership.operational_owner` missing/empty).
2. **No machine-executable rollback target** (absent or not dry-runnable).
3. **No evaluation suite** mapping a non-deterministic AI feature (each `prompts.*` / model role must reference a resolving `eval_suite`/`eval_baseline`).
4. **Broken alert→runbook link** (any `alert.runbook` path does not resolve to a real file).

## Library / SDK Adapter (red-line interpretation)

DPK-1.0 red-lines are service-centric. For a **library/SDK** (`repository.type: library`) map them to the packaging equivalents — the red line still holds, only its evidence changes:

- **Ops owner** → the package **maintainer / owning team** (still required; `Unknown` fails).
- **Rollback** → a **version pin/yank** target (`npm dist-tag` + `npm deprecate`, or a yanked release) that is real and dry-runnable, plus a **golden-parity gate** as the safety net.
- **Eval suite** → N/A when the library is deterministic (no non-deterministic AI feature); the parity/golden-vector suite is the equivalent proof.
- **Alert→runbook** → a **CI parity-failure signal** (cross-language vector mismatch) routed to a documented response, in place of a production page.

## Default Ownership & Autofix (ON by default)

Missing infrastructure decisions are **auto-filled to safe org defaults instead of failing** — `validate_devpack.py` runs autofix by default:

- **Ops owner** → defaults to **`quantum-ai`** (the org owner) when `operational_owner` is unspecified/placeholder. This is a *declared default ownership policy* ("if not otherwise specified, Quantum AI owns it"), not a fabrication — the report records it under `autofixes`. Override the default with `--owner <team>`.
- **Rollback (library/SDK only)** → defaults to the version pin/yank target (`npm dist-tag` + `npm deprecate`), which *is* a package's rollback mechanism.

Autofix never invents things it cannot safely default: a **broken runbook link** or a **missing eval suite for a real AI feature** still fails (fabricating those would hide real risk). Run `--strict` to disable autofix and restore fail-closed behavior (every unspecified red-line fails) for a hard pre-handoff audit.

When the compiler emits a pack (Gate D), it applies the same defaults: an unspecified `operational_owner` is written as `quantum-ai`, and a library's rollback is written as the version-pin/yank target.

A library run with `--strict` that leaves the maintainer or rollback `Unknown` is still `blocked` — the decision is undocumented, not absent-by-nature.

## Verdict

- `operable` — score ≥ 90, no red-line.
- `conditional` — score 80–89, no red-line.
- `blocked` — score < 80 **or** any red-line tripped. Never present a `blocked` pack as operable; deliver it with the specific failing red-line/category and its remediation target.

## Output

`scripts/validate_devpack.py` emits a machine-readable report: per-category status, red-line verdicts, total score, band, and the ranked remediation list. Exit codes: `0` operable/conditional, `1` blocked (red-line or score<80), `2` unreadable input.
