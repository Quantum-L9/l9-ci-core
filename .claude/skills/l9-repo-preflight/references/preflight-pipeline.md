<!-- L9_META
l9_schema: 1
parent: l9-repo-preflight
layer: reference
role: preflight_pipeline
tags: [preflight, eight-gates, decision-tree, failure-taxonomy, golden-rules]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-15
/L9_META -->

# The Eight-Gate Preflight Contract

Faithful to the 10X Repository Preflight Decision Tree. Gates are **sequential and fail-closed**: gate _N_ is evaluated only after gate _N−1_ passes. Every **NO** exits through `Fix → Re-run Probe → Verify → Continue` — a gate is never waved through on assumption.

## Golden Rules (the red lines behind every gate)

1. Never continue with **unknown files**.
2. Never continue if the **baseline cannot be reproduced**.
3. Never modify **code** until repository facts are verified.
4. Never adapt the **repository to the blueprint** — adapt the blueprint to verified evidence.
5. Every **NO loops back** through the probe.

## Verdict vocabulary

`pass` · `blocked` (stop-and-fix) · `confirm` (needs the expected contract or a human) · `adapt` (the blueprint is wrong for this repo). `ready` is true only when gates 1–7 are `pass` and no red line is tripped.

---

## Gate 1 — Did the probe complete successfully?

- **Question:** did the probe run end-to-end on this checkout?
- **PASS:** the log carries every section marker and ends with `PROBE COMPLETE`; no fatal shell/git error.
- **Failure taxonomy:**
  - **shell error** → fix syntax → re-run probe.
  - **git error** → verify repo + verify git is available → re-run probe.
  - **missing command** → install tool + verify PATH → re-run probe.
- **Note:** the probe wraps commands in `safe()`, so individual `[command failed: …]` lines are evidence, not fatal — Gate 1 fails only if the run did not complete.

## Gate 2 — Correct repository / branch / commit?

- **Question:** is this the repo, branch, and commit the work targets?
- **PASS:** identity (root, remotes, branch, HEAD) matches the expected contract, or is explicitly human-confirmed.
- **No expected contract → `confirm`:** report observed identity and require confirmation; never assume.
- **Failure taxonomy:**
  - **wrong repo** → re-clone → re-run probe.
  - **wrong branch** → switch branch → re-run probe.
  - **wrong commit** → fetch / reset → re-run probe.

## Gate 3 — Worktree clean?

- **Question:** is the tree free of unexpected changes?
- **PASS:** `TRACKED_MODIFIED_COUNT = 0`, `STAGED_COUNT = 0`, and every untracked file is a known-generated, ignored artifact.
- **Failure taxonomy (what is dirty?):**
  - **tracked files** → were these edits expected? YES: review diffs, commit or stash, continue. NO: classify (source / config / docs) → compare with the execution plan → keep or revert. Revert path: `git restore` / `git stash` / reset → re-run probe.
  - **generated files** → expected artifacts (`.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.egg-info`)? If ignored → continue. If not ignored → **autofix**: update `.gitignore`, remove local files, verify nothing tracked → re-run probe.
  - **unknown files** → **RED LINE (Golden Rule 1).** Do you know where they came from? NO → **DO NOT CONTINUE**: scan filenames, inspect contents, check timestamps and recent commands. Safe → ignore / update plan, continue. Not safe → remove / revert / re-clone → re-run probe. Never `ready` while an unknown file stands.

## Gate 4 — Required project foundations present?

- **Question:** are the foundations the work assumes actually here?
- **PASS:** every expected foundation resolves to a real path.
- **Failure taxonomy (which is missing — pyproject / tests / src / AGENTS?):**
  - **expected in this repo** → wrong checkout / wrong branch / partial clone → re-run probe.
  - **not expected in this repo → `adapt` (Golden Rule 4):** wrong assumption in the blueprint → update the blueprint, adapt the execution contract. A missing `src/` or `AGENTS.md` in a repo that never had them is **not** a failure — it is a blueprint correction.

## Gate 5 — Toolchain matches the execution contract?

- **Question:** do python / package-manager / test-tools satisfy the contract?
- **PASS:** the required interpreter, package manager, and test tools are present and version-compatible.
- **Failure taxonomy (what doesn't match — python / package mgr / test tools?):**
  - **repository already defines one** → **follow the repository; do NOT replace existing tooling** → re-run validation.
  - **repository defines none** → record a blocker, decide later.
- **Note:** config may live outside `pyproject.toml` (e.g. `ruff.toml`, `mypy.ini`) and pins outside it (e.g. `requirements-ci.txt`). Read the repo's real toolchain, not just `[tool.*]`.

## Gate 6 — Installation succeeded?

- **Question:** does the declared install method succeed and are packages importable?
- **PASS:** the repo's install command exits 0 and the target packages import.
- **Failure taxonomy (why did install fail — dependency / build backend / editable install?):**
  - **repository problem** → record the baseline, **do not edit code yet** → re-run install.
  - **environment problem** → fix python / recreate venv / retry install → re-run install.

## Gate 7 — Baseline validation passes?

- **Question:** does the baseline (pytest / mypy / ruff / schema) reproduce?
- **PASS:** the validation suite runs and any failures are **existing** baseline, not new.
- **Failure taxonomy (which failed — pytest / mypy / ruff / schema?):**
  - **existing failure** → record evidence; continue if non-blocking. A known-red repo is still preflightable — the current failures are the baseline.
  - **new failure** → **RED LINE (Golden Rule 2).** Stop implementation; fix immediately before continuing.
- **Classification rule:** on the first probe with no prior edits, all failures are existing baseline. After any edit, compare against the recorded baseline; anything not in it is `new`.

## Gate 8 — Implementation ready?

- **Question:** is the checkout safe to build on?
- **PASS → START IMPLEMENTATION.** Gates 1–7 pass and no red line is tripped.
- **NO:** take the **smallest blocker first**, resolve it, re-run the affected stage, and re-run the full probe if necessary. Emit the readiness report with `ready: false` and the single next action.

---

## The loop

```text
        +-------------------- NO --------------------+
        |                                            |
   Run Probe --> Evaluate Gate --> PASS? --> ... --> Ready?
        ^            |                                |
        |          Fix (smallest blocker)             YES
        +---- Re-run Probe <---- Verify <-------------+ --> START IMPLEMENTATION
```

Every NO returns to the top. No gate is verified without a fresh probe behind it (see [enforcement-gates.md](enforcement-gates.md), Gate F).
