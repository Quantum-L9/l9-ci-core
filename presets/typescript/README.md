# L9 Governed CI — TypeScript Preset

This directory contains the **locked, canonical CI configuration** for all TypeScript/JavaScript repositories in the Quantum-L9 organization.

It provides a zero-friction, copy-paste deployment of the `l9-ci-core` governed pipeline, mirroring the `presets/python` preset.

## What's Included

| File | Purpose | Lock Status |
|------|---------|-------------|
| `.github/workflows/l9-analysis.yml` | The governed analysis pipeline (Semgrep, SDK, Manifests) | **100% Locked** (TypeScript/JavaScript config only) |
| `.github/workflows/l9-lint-test.yml` | Code hygiene (ESLint, tsc, test runner) | **Structure Locked**, Env configurable |
| `.github/governance/*.yaml` | Governance rules (Profiles, Modes, Waivers) | **100% Locked** |

## How to Activate CI (For Humans)

If you are setting up a new TypeScript/JavaScript repository:

1. Copy the `.github` directory from this preset into your repository root:
   ```bash
   cp -r path/to/l9-ci-core/presets/typescript/.github ./
   ```
2. Open `.github/workflows/l9-lint-test.yml` and update the `env:` block at the top:
   - `NODE_VERSION`: the Node.js version your repo targets, e.g. `"20"`
   - `PACKAGE_MANAGER`: `"npm"`, `"pnpm"`, or `"yarn"`
   - `SOURCE_DIR`: e.g., `"src/"` or `"."`
   - `HAS_TYPESCRIPT`: `"true"` if the repo uses TypeScript (has `tsconfig.json`), `"false"` for plain JavaScript
3. Ensure `package.json` defines a `lint` script (falls back to `npx eslint` if absent) and a `test` script (skipped with a notice if absent).
4. Commit and push.

## How to Activate CI (For AI Agents)

Agents (Cursor, Manus, Claude Code) should be instructed to:
> "Activate L9 CI using the typescript preset"

They will automatically execute the `l9-ci-activation-typescript` skill, which handles directory discovery and variable injection deterministically.

## GitHub Starter Workflow Integration

If your organization has a `.github` repository for starter workflows, you can copy the contents of `starter-workflows/typescript/` from `l9-ci-core` into it.

This will add "L9 Governed TypeScript CI" as a one-click setup option in the **Actions** tab of every new repository in the organization.
