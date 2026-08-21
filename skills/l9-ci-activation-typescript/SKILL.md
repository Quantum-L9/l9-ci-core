# Skill: L9 CI Activation (TypeScript)

> **LEGACY (frozen).** This copy-first seeding protocol is superseded by the
> organization-facing Core entrypoint (`.github/workflows/org-ci.yml`,
> `l9.org-runtime-contract/v1`), selected by l9-ci-control-plane at a full
> immutable Core SHA. Do not use this skill for new consumers; see
> [`presets/LEGACY.md`](../../presets/LEGACY.md).

## Purpose
This skill provides the exact deterministic protocol for an AI agent to activate L9 CI in any TypeScript/JavaScript repository. It uses the locked `typescript` preset from `Quantum-L9/l9-ci-core` to ensure zero-friction, zero-ambiguity setup.

## Capabilities
- Provides the protocol for enabling governed L9 CI in a TypeScript/JavaScript repo
- Defines how to discover the four repo-specific variables (`NODE_VERSION`, `PACKAGE_MANAGER`, `SOURCE_DIR`, `HAS_TYPESCRIPT`)
- Stamps the locked Biome contract (`biome.json`, `.biomeignore`, `.editorconfig`, plugin recommendation) — agents never hand-author these files
- Establishes the exact copy commands to pull the locked preset

## Instructions for Agent

When tasked to "activate CI", "setup CI", "enable l9-ci-core", or "add Biome" in a TypeScript or JavaScript repository, follow these exact steps. **Do not deviate, do not modify the locked preset files, and do not ask the user for configuration details.**

### Step 1: Discover Repository Variables

Inspect the target repository to determine these four values:

1. **`NODE_VERSION`**:
   - Look at `.nvmrc`, `.node-version`, or the `engines.node` field in `package.json`.
   - If none found, default to `"20"`.

2. **`PACKAGE_MANAGER`**:
   - If `pnpm-lock.yaml` exists, set `"pnpm"`.
   - Else if `yarn.lock` exists, set `"yarn"`.
   - Else if `package-lock.json` exists (or none of the above), set `"npm"`.

3. **`SOURCE_DIR`**:
   - If `src/` exists, set `SOURCE_DIR="src/"`.
   - Otherwise, set `SOURCE_DIR="."`.

4. **`HAS_TYPESCRIPT`**:
   - If `tsconfig.json` exists at the repo root, set `"true"`.
   - Otherwise, set `"false"`.

### Step 2: Copy the Locked Preset

Clone `Quantum-L9/l9-ci-core` to a temporary directory, then copy the entire `presets/typescript/.github` folder into the target repository root.

```bash
git clone https://github.com/Quantum-L9/l9-ci-core.git /tmp/l9-ci-core
mkdir -p .github
cp -r /tmp/l9-ci-core/presets/typescript/.github/* .github/
```

### Step 2b: Stamp Biome (do not hand-author `biome.json`)

Run the preset stamp script against the consumer repo root. It copies the locked Biome 2.5.8 contract, `.biomeignore`, `.editorconfig`, and the `biomejs.biome` extension recommendation. Existing `biome.json` is kept.

```bash
bash /tmp/l9-ci-core/presets/typescript/stamp.sh "$(pwd)"
```

**CRITICAL RULE:** Never invent, draft, or rewrite `biome.json` by hand. Never copy a consumer-specific `biome.json` from another product repo and treat it as the contract. Extra path excludes may be **appended** to `files.includes` after the stamp; the `formatter`, `linter`, `javascript`, `json`, and `overrides` blocks stay locked.

**CRITICAL RULE:** Do not add ESLint or Prettier as a second JS/TS/JSON owner. Biome owns format + lint via the SDK reusable workflow. No `lint` script is required. If `package.json` already has `lint`, leave it unless the operator asked to point it at `biome check .`.

After `biome.json` exists, real-time editor lint is owned by Cursor-Governance `install_ide_profile.sh` (workspace class `biome_default`). Do not hand-author `.vscode/settings.json`.

### Step 3: Inject Variables

Edit ONLY the `env:` block in `.github/workflows/l9-lint-test.yml` using the variables discovered in Step 1. The `biome` job's `with:` inputs stay at the preset defaults (`scan-path: "."`, `enforce-biome: false`) unless the operator asked to change them.

```yaml
env:
  NODE_VERSION: "<YOUR_DISCOVERED_NODE_VERSION>"
  PACKAGE_MANAGER: "<YOUR_DISCOVERED_PACKAGE_MANAGER>"
  SOURCE_DIR: "<YOUR_DISCOVERED_SOURCE_DIR>"
  HAS_TYPESCRIPT: "<YOUR_DISCOVERED_HAS_TYPESCRIPT>"
```

**CRITICAL RULE:** Never modify `.github/workflows/l9-analysis.yml` or any file in `.github/governance/`. They are 100% locked and managed centrally by the L9 platform.

**CRITICAL RULE:** If the repository has no `test` script, the test step is skipped with a `::notice::` and does not fail the run — do not fabricate a test script.

### Step 4: Commit and Push

Stage `.github/`, `biome.json`, `.biomeignore`, `.editorconfig`, and `.vscode/extensions.json`, commit with message `ci: activate l9-ci-core governed pipeline`, and push.

---
**Skill Metadata**
- **Domain:** L9 CI Platform
- **Target:** TypeScript/JavaScript repositories
- **Version:** 2.1.0
