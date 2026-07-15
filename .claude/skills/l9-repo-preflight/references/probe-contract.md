<!-- L9_META
l9_schema: 1
parent: l9-repo-preflight
layer: reference
role: probe_contract
tags: [preflight, probe, evidence, section-to-gate, parameterizable]
owner: igor_beylin
status: active
version: 1.0.0
updated: 2026-07-15
/L9_META -->

# Probe Contract (evidence → gate mapping)

The probe ([scripts/preflight_probe.sh](../scripts/preflight_probe.sh)) is **read-only**: it writes nothing but a timestamped log. It emits labeled sections (`===== NAME =====`); the evaluator ([scripts/evaluate_preflight.py](../scripts/evaluate_preflight.py)) parses those sections and feeds them to the gates. This file is the map.

## Section → gate

| Probe section | Feeds gate | Used for |
|---|---|---|
| `TIMESTAMP` | 1 | run identity; freshness (must post-date the last fix) |
| `REPOSITORY IDENTITY` | 2 | root, git-dir, branch, HEAD, subject/author/date |
| `REMOTES` | 2 | origin URL vs expected repo |
| `TRACKING AND DIVERGENCE` | 2 | upstream, ahead/behind |
| `WORKTREE STATUS` | 3 | `TRACKED_MODIFIED_COUNT`, `UNTRACKED_COUNT`, `STAGED_COUNT`, `UNSTAGED_COUNT` |
| `DIFF SUMMARY` | 3 | tracked/staged/untracked file lists for classification |
| `IGNORE DIAGNOSTICS` | 3 | which generated artifacts are ignored vs tracked |
| `TOP-LEVEL INVENTORY` | 3, 4 | what actually exists at the tree top |
| `KEY FILE PRESENCE` | 4 | foundations present/missing (`present`/`missing` lines) |
| `PYTHON TOOLCHAIN` | 5 | interpreter paths + versions, venv, pip |
| `PROJECT METADATA` | 4, 5 | `pyproject.toml` build-system / project / tool keys |
| `PACKAGE DISCOVERY` | 5, 6 | source files + importability (`NAME=… / NOT_IMPORTABLE`) |
| `TEST INVENTORY` | 7 | test files + estimated test count |
| `VALIDATION TOOL AVAILABILITY` | 5, 7 | pytest/ruff/mypy/… present or `MISSING` |
| `MAKE TARGETS` / `PYPROJECT COMMAND CONFIG` | 5, 6 | declared commands / install + validation entry points |
| `CI WORKFLOWS` | 5, 7 | what CI runs (the authoritative validation contract) |
| `GIT HOOKS AND ATTRIBUTES` | 3 | `.gitignore` / `.gitattributes` / hooks path |
| `LARGE AND SUSPICIOUS FILES` | 3 | oversized / unexpected artifacts |
| `COMMON GENERATED ARTIFACTS` | 3 | generated dirs present (classification input) |
| `SUBMODULES AND LFS` | 2, 4 | submodule / LFS state |
| `RECENT HISTORY` | 2 | last commits (identity corroboration) |
| `FINAL CLEANLINESS` | 3 | end-of-probe status + `FINAL_HEAD` |
| `PROBE COMPLETE` | 1 | completion marker (its absence fails Gate 1) |

## The parameterizable surface (evidence overrides blueprint)

The probe carries a small config block of **repo-specific tokens** — the only thing that changes between repos. They are a **hypothesis**, not a spec; verified evidence overrides them (Golden Rule 4).

| Token | Meaning | Default (this repo) |
|---|---|---|
| `PROBE_PACKAGES` | importable packages to check | `l9_bootstrap` |
| `PROBE_KEY_PATHS` | key dirs to inventory in KEY FILE PRESENCE | `.github/scripts .github/scripts/l9_bootstrap tests schemas .github/workflows` |
| `PROBE_FOUNDATIONS` | foundations Gate 4 expects | `pyproject.toml tests schemas` |

Override per repo via environment variables, e.g.:

```bash
PROBE_PACKAGES="my_pkg my_other_pkg" \
PROBE_FOUNDATIONS="pyproject.toml src tests" \
bash scripts/preflight_probe.sh
```

### Why parameterized, not hardcoded

The source probe was authored against a different repo (an `src/`-layout service with packages `l9_ops_mcp` / `l9_action_governor`). This repo (`l9-ci-core`) has **no `src/`**, keeps Python under `.github/scripts/` (sole package `l9_bootstrap`), and has no `AGENTS.md` or `Makefile`. Hardcoding the foreign tokens would make Gate 4/5 report false failures on every run. Lifting them into an overridable block lets the **same probe** run anywhere, and lets the evaluator treat a foreign expectation that the repo does not meet as an `adapt` (fix the blueprint), never a `blocked` (fail the repo).

## The expected contract (optional, for Gate 2/4/5)

Provide `--expected <contract>` to `evaluate_preflight.py` to turn Gate 2 `confirm` into a decidable `pass`/`blocked` and to give Gates 4/5 an explicit foundations/toolchain target. Schema: [schemas/expected-contract.schema.json](../schemas/expected-contract.schema.json). The contract is authority level 3 — **below verified evidence** (level 2): where they disagree, evidence wins and the gate returns `adapt`.
