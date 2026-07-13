<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: finding_classifier
tags: [pr, classification, triage, severity, validation-doctrine, confidence-gate]
owner: igor_beylin
status: active
version: 3.2.0
updated: 2026-07-13
/L9_META -->

# Finding Classifier & Validation Doctrine

## Purpose

Classify each ingested finding by severity and fix strategy, **validate each review suggestion against the current code**, and decide — per suggestion — whether to auto-apply, validate-then-apply, defer, or ignore. Review bots have a high false-positive rate; validation is mandatory, not optional.

## Severity Classification

| Severity | Definition | Action |
|----------|-----------|--------|
| **blocking** | CI gate failure that prevents merge | Fix immediately, cycle-1 priority |
| **actionable** | Review comment with a clear, implementable, validated suggestion | Fix after blocking items |
| **discussion** | Question / alternative proposal, no concrete fix | Skip; report (Acknowledged) |
| **deferred** | Needs human decision, architectural change, or external dependency | Skip; report with reason + issue |

## CI Failure Classification

| Gate Type | Indicators | Fix Strategy |
|-----------|-----------|--------------|
| lint | ruff, eslint, biome | Linter `--fix`, then manual for unfixable |
| format | prettier, ruff format | Run formatter (always safe) |
| type-check | tsc, mypy, pyright | Fix annotations/interfaces; never `any`/`@ts-ignore` |
| test | jest, pytest, vitest | Fix code first; test expectations only if intentionally changed |
| build | tsc --noEmit, vite build | Fix imports/modules/syntax |
| security | npm audit, snyk, trivy, sonarcloud | Update deps / apply patch; never suppress without approval |

## Review-Comment Classification (per suggestion)

Assign each review finding one disposition:

| Disposition | Meaning | Gate |
|-------------|---------|------|
| **AUTO_APPLY** | Deterministic + safe (formatter output, exact suggestion block that still matches, obvious typo) | Apply directly |
| **VALIDATE** | Plausible but must be checked against current code before applying | Must pass validation + confidence gate |
| **DEFER** | Correct-but-out-of-scope, needs human decision, or below confidence gate | Create issue, reply Deferred |
| **IGNORE** | False positive, already handled, or pure discussion | Reply Disagree/Acknowledged; no code change |

### Actionable indicators
Contains a suggestion block or inline code; points to a concrete bug ("throws because X is undefined"); names a type mismatch, missing import, wrong property, or incorrect API usage; says "should be X" / "change Y to Z".

### Discussion indicators
"Have you considered…", "what about…"; architectural alternative with no concrete fix; questions a decision without asserting it is wrong; `nit:` prefix (unless a clear one-line fix); requests explanation.

### Deferred indicators
Needs a new dependency/service; a refactor spanning files outside the PR diff; conflicts with another comment; needs owner direction; references inaccessible external systems.

## Validation Doctrine (CRITICAL — run before applying anything)

Read the **actual current file** (never the comment snippet). Reject or defer a suggestion when it:

- references code that no longer exists or has changed since the comment;
- would break a passing gate or introduce a type/compile error;
- contradicts an explicit project convention or the configured linter/formatter;
- is a stylistic nit conflicting with the formatter;
- proposes an architectural change beyond the PR's scope;
- conflicts with a higher-precedence suggestion on the same lines.

**Reject explicitly with a reason** (feeds a `Disagree` reply). Never silently skip. Bot "unused variable/import" flags are a common false-positive class — verify closures and re-exports before touching code.

## Confidence Gate

Assign each `VALIDATE` finding a confidence score `0.0–1.0`. If `confidence < confidence_gate` (default `0.75`) → reclassify as **DEFER**. Score from: suggestion specificity, agreement with repo conventions, and whether the referenced code still matches.

### Confidence-Gate Self-Tuning (drift signal)

The gate is not a static `0.75` — it reads the `bot_false_positive_rate` drift signal so a chronically-wrong reviewer earns less trust automatically:

- Over a run, compute per reviewer `fp_rate = rejected / (applied + rejected)` (0 when no decided findings). This is emitted in the run report at `summary.bot_false_positive_rate` (see `references/review-replies.md` and `schemas/run-report.schema.json`).
- Raise the **effective** gate for a reviewer as its FP rate climbs, bounded to `[0.60, 0.90]`:

  ```text
  effective_gate(reviewer) = clamp(confidence_gate + 0.30 * fp_rate, 0.60, 0.90)
  ```

  A reviewer with `fp_rate = 0` keeps the base gate; one at `fp_rate = 0.5` needs ≥ `0.90` confidence to auto-apply.
- Apply only to bot reviewers; human suggestions are not down-weighted by this rule (authority order still puts human first).
- This makes false-positive suppression **compound**: every explicit rejection (a `Disagree` reply) tightens the gate for that bot on future cycles instead of being a one-off.

## Conflict Precedence

When findings conflict on the same lines, resolve deterministically:

```text
human > blocking(CI) > higher_confidence > more_recent
```

- CI requirement always beats a review suggestion (CI blocks merge).
- Two humans conflicting with no precedence → **DEFER** to user.

## Execution Priority

```text
1. blocking (CI)         — build > type-check > lint > test > security
2. AUTO_APPLY review     — safe, deterministic
3. VALIDATE review (>=gate) — file proximity to CI failures first, then top-to-bottom
4. discussion / DEFER / IGNORE — reported, no code change
```

## Output (Gate B artifact)

```yaml
classified_findings:
  blocking: [{finding}...]
  auto_apply: [{finding}...]
  validate: [{finding, confidence}...]
  discussion: [{finding}...]
  deferred: [{finding, reason}...]
  ignored: [{finding, reason}...]   # false positives with rejection reason

execution_plan:
  cycle_scope: [finding IDs to fix this cycle]   # blocking + auto_apply + validate>=gate
  estimated_files: [files to modify]
  local_verify_commands: [ALL gate commands from the registry]
```

## Batch Planning Rule

The plan MUST include ALL findings to fix this cycle — never one at a time. Fix all blocking → all accepted review items → run ALL local verify commands → one commit → one push.
