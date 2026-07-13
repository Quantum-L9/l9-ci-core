<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: fix_engine
tags: [pr, fix, code, methodology, local-verify, batch, rollback]
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-07-13
/L9_META -->

# Fix Engine

## Purpose

Apply fixes for the accepted findings (blocking CI + AUTO_APPLY + validated VALIDATE items). Each fix type has a methodology that minimizes regression risk. ALL fixes for a cycle are batched, verified locally, and committed as ONE unit per PR.

## Hard Rules

- MUST read the target file before editing.
- MUST understand surrounding context (imports, types, dependencies).
- MUST NOT change code unrelated to the finding.
- MUST NOT introduce new warnings or errors.
- MUST NOT commit or push until ALL fixes are applied AND local verify passes.
- MUST use the smallest diff that resolves the finding.
- When a fix requires changes outside the PR's file scope → mark deferred, do not expand scope.
- NEVER push partial fixes — all or nothing per cycle.

## Gate Discovery (already produced in signal-ingestion)

Use the gate registry built during ingestion as the **local verify command list**. Cross-check `package.json` scripts (`lint`, `typecheck`, `test`, `build`, `validate`), any `Makefile` `ci`/`pr-check` target, and `.husky/` pre-commit hooks. Every `run:` command that can exit non-zero must be in the list.

## Fix Strategies by Type

### Lint
```bash
npx eslint --fix {file}        # JS/TS
ruff check --fix {file}         # Python
npx biome check --apply {file}  # Biome
```
If auto-fix fails, read the rule and apply manually.

### Format — always safe
```bash
npx prettier --write {file}
ruff format {file}
```

### Type-check
1. Read the exact error (file, line, expected vs actual). 2. Check the type source. 3. Apply the minimal fix: missing property → add with correct type; wrong type → narrow/fix source value; missing import → add; optional/required mismatch → `?` or default. 4. NEVER use `any` or `@ts-ignore`.

### Test
Priority: fix the code under test (real bug) → fix the assertion (only if code intentionally changed) → fix stale fixtures. NEVER delete a failing test unless the feature was intentionally removed in the PR.

### Build
Missing module → import/install; syntax error → fix; circular dependency → restructure imports; missing file → check if deleted accidentally.

### Security
Dependency vuln → update to patched version; code vuln → apply remediation; NEVER suppress without explicit user approval.

### Review-comment fixes (per disposition)
- **AUTO_APPLY** suggestion block: apply exactly if it still matches the current file.
- **VALIDATE** bug report: read code, confirm the bug, apply minimal fix.
- Property/name correction: verify against type defs, then rename.
- Missing null check: add the guard.
- Performance suggestion: apply only if clearly correct and low-risk.

## Local Verification Protocol (BLOCKING GATE — Gate D)

After applying ALL fixes for the cycle, run EVERY command in the local verify list:

```bash
# Run ALL gates — do NOT stop at first failure
npx tsc --noEmit
npx eslint . --max-warnings 0
npx vitest run
npm run build
# ...every gate from the registry
```

Rules:
1. **Run ALL gates**, not just the one that was failing — a fix for one can break another.
2. **If ANY gate fails** → fix immediately, then re-run ALL gates from scratch.
3. **Repeat until ALL pass.** Only then commit.
4. **If a fix for gate A breaks gate B** → the fix is wrong; revert and find a better one.
5. **Circular regression** (A breaks B, B breaks A) → defer both with that reason.
6. **Max local verify iterations: 5.** If not green after 5, defer the problematic findings and verify the rest.

"Locally" means the exact command CI runs (workflow `run:` field), same runtime version where possible. If a gate needs secret env vars, check for a `--ci`/`--skip-secrets` flag; if it needs external services, check for a dry-run/mock mode.

## Batch Discipline

```text
WITHIN A SINGLE CYCLE (per PR):
  1. Apply fix for finding-1 … finding-N
  2. Run ALL local verify gates
  3. Fix any new failures; re-run ALL gates
  4. Repeat until green
  5. git add -A && git commit   (ONE commit)
  6. git push                   (ONE push)

  NEVER commit after each fix. NEVER push without local verify green.
  NEVER push more than once per cycle.
```

## Commit Convention (ONE per cycle)

```
fix(pr-remediation): cycle {N} — resolve {count} findings

Fixes:
- {finding-id}: {one-line description}

Local verify: all {N} gates passed
Deferred:
- {finding-id}: {reason}
```

## Rollback Protocol

If a fix introduces a NEW CI failure that did not exist before:
1. `git diff HEAD~1` — identify the problematic change.
2. Revert only that change.
3. Mark the original finding `deferred` — reason "fix causes regression".
4. Re-run local verify to confirm the revert is clean.
5. Continue with remaining fixes.
6. Report the regression in the convergence block.
