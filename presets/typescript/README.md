# L9 Governed CI — TypeScript Preset

This directory contains the **locked, canonical CI configuration** for all TypeScript/JavaScript repositories in the Quantum-L9 organization.

It provides a zero-friction, copy-paste deployment of the `l9-ci-core` governed pipeline, mirroring the `presets/python` preset.

## What's Included

| File | Purpose | Lock Status |
|------|---------|-------------|
| `.github/workflows/l9-analysis.yml` | The governed analysis pipeline (Semgrep, SDK, Manifests) | **100% Locked** (TypeScript/JavaScript config only) |
| `.github/workflows/l9-lint-test.yml` | Code hygiene: **Biome** (format + lint, SDK-owned reusable workflow), `tsc --noEmit`, test runner | **Structure Locked**, Env + biome `with:` configurable |
| `.github/governance/*.yaml` | Governance rules (Profiles, Modes, Waivers) | **100% Locked** |
| `biome.json` | Locked Biome 2.5.8 contract (format + lint + assist) | **Locked** — stamp, do not hand-author |
| `.biomeignore` | Generated-tree exclusions (keep in sync with `files.includes`) | **Locked** |
| `.editorconfig` | Editor-agnostic indent/newline contract Biome honors | **Locked** |
| `.vscode/extensions.json` | Recommends `biomejs.biome` for real-time editor lint | **Locked** |
| `stamp.sh` | Copies the files above into a consumer repo without inventing config | **Locked** |

## Formatter/linter ownership

Biome owns JS/TS/JSON **format + lint** via the SDK-owned reusable workflow
`Quantum-L9/l9-ci-sdk/.github/workflows/l9-biome-scan.yml` (pinned to a full
commit SHA). ESLint is **not** a second formatter owner in this preset — a repo
may keep ESLint only for supplemental rules Biome does not cover, never for
formatting. `tsc --noEmit` (type check) and the repository test suite stay in
your repo. See [`docs/consumer-lint-test.md`](../../docs/consumer-lint-test.md).

## How to Activate CI (For Humans)

If you are setting up a new TypeScript/JavaScript repository:

1. Copy the `.github` directory from this preset into your repository root:
   ```bash
   cp -r path/to/l9-ci-core/presets/typescript/.github ./
   ```
2. Open `.github/workflows/l9-lint-test.yml` and update the `env:` block at the top:
   - `NODE_VERSION`: the Node.js version your repo targets, e.g. `"20"`
   - `PACKAGE_MANAGER`: `"npm"`, `"pnpm"`, or `"yarn"`
   - `SOURCE_DIR`: e.g., `"src/"` or `"."` (used by `tsc`/tests)
   - `HAS_TYPESCRIPT`: `"true"` if the repo uses TypeScript (has `tsconfig.json`), `"false"` for plain JavaScript
3. Configure the `biome` job's `with:` inputs (reusable-workflow inputs cannot
   read `env:`, so they are set on the job directly):
   - `scan-path`: path passed to `biome ci`, e.g. `"."` or `"src"`.
   - `enforce-biome`: rollout flag (see below).
4. Stamp the locked Biome contract — **do not hand-author `biome.json`**:
   ```bash
   bash path/to/l9-ci-core/presets/typescript/stamp.sh "$(pwd)"
   ```
   Extra path excludes may be appended to `files.includes` after the stamp.
   Do not rewrite the `formatter` / `linter` / `javascript` / `json` blocks.
5. Ensure `package.json` defines a `test` script (skipped with a notice if
   absent). No `lint` script is required — Biome owns format + lint.
6. Commit and push. Real-time editor lint is then `install_ide_profile.sh`
   (Cursor-Governance) once `biome.json` is present.

## Advisory-to-blocking rollout

The `biome` job invokes the SDK reusable workflow with an `enforce-biome`
input that controls the rollout stage:

| `enforce-biome` | Behavior | When |
|---|---|---|
| `false` (default) | Full scan, annotate findings, **exit 0** (advisory). | Initial adoption — surface Biome findings without blocking merges. |
| `true` | Fail the job on Biome findings (**blocking**). | Once the repo is clean, flip to enforce format + lint on every PR. |

Roll out by adopting at `enforce-biome: false`, driving the codebase to zero
Biome findings, then editing the single `with:` line to `enforce-biome: true`.
This staged rollout mirrors the Semgrep rule `disabled → shadow → advisory →
blocking` promotion discipline.

## How to Activate CI (For AI Agents)

Agents (Cursor, Manus, Claude Code) should be instructed to:
> "Activate L9 CI using the typescript preset"

They will automatically execute the `l9-ci-activation-typescript` skill, which
handles directory discovery, variable injection, and Biome stamping
(`stamp.sh`) so agents never invent `biome.json`.

## GitHub Starter Workflow Integration

If your organization has a `.github` repository for starter workflows, you can copy the contents of `starter-workflows/typescript/` from `l9-ci-core` into it.

This will add "L9 Governed TypeScript CI" as a one-click setup option in the **Actions** tab of every new repository in the organization.
