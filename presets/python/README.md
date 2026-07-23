# L9 Governed CI — Python Preset

This directory contains the **locked, canonical CI configuration** for all Python repositories in the Quantum-L9 organization.

It provides a zero-friction, copy-paste deployment of the `l9-ci-core` governed pipeline.

## What's Included

| File | Purpose | Lock Status |
|------|---------|-------------|
| `.github/workflows/l9-analysis.yml` | The governed analysis pipeline (Semgrep, SDK, Manifests) | **100% Locked** (Python config only) |
| `.github/workflows/l9-lint-test.yml` | Code hygiene (Ruff, Mypy, Pytest) | **Structure Locked**, Env configurable |
| `.github/governance/*.yaml` | Governance rules (Profiles, Modes, Waivers) | **100% Locked** |

## How to Activate CI (For Humans)

If you are setting up a new Python repository:

1. Copy the `.github` directory from this preset into your repository root:
   ```bash
   cp -r path/to/l9-ci-core/presets/python/.github ./
   ```
2. Open `.github/workflows/l9-lint-test.yml` and update the `env:` block at the top:
   - `SOURCE_DIR`: e.g., `"src/"` or `"."`
   - `TEST_DIR`: e.g., `"tests/"`
   - `PYTEST_PARALLEL`: `"true"` to opt into `pytest -n auto`. Default `"false"` —
     leave off unless your suite is verified safe under xdist (parallel workers
     can produce nondeterministic collection for suites with shared state).
   - `MYPY_EXCLUDE`: optional regex passed to `mypy --exclude`. Use this to
     mirror your repo's own `.pre-commit-config.yaml` mypy excludes (e.g. a
     `tools/` directory that causes "Source file found twice under different
     module names" when scanned without `__init__.py`/`--explicit-package-bases`).
3. Commit and push.

## How to Activate CI (For AI Agents)

Agents (Cursor, Manus, Claude Code) should be instructed to:
> "Activate L9 CI using the python preset"

They will automatically execute the `l9-ci-activation` skill, which handles directory discovery and variable injection deterministically.

## GitHub Starter Workflow Integration

If your organization has a `.github` repository for starter workflows, you can copy the contents of `starter-workflows/python/` from `l9-ci-core` into it. 

This will add "L9 Governed Python CI" as a one-click setup option in the **Actions** tab of every new repository in the organization.
