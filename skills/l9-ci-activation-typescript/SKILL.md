# Skill: L9 CI Activation (TypeScript)

## Purpose
This skill provides the exact deterministic protocol for an AI agent to activate L9 CI in any TypeScript/JavaScript repository. It uses the locked `typescript` preset from `Quantum-L9/l9-ci-core` to ensure zero-friction, zero-ambiguity setup.

## Capabilities
- Provides the 3-step protocol for enabling governed L9 CI in a TypeScript/JavaScript repo
- Defines how to discover the four repo-specific variables (`NODE_VERSION`, `PACKAGE_MANAGER`, `SOURCE_DIR`, `HAS_TYPESCRIPT`)
- Establishes the exact copy commands to pull the locked preset

## Instructions for Agent

When tasked to "activate CI", "setup CI", or "enable l9-ci-core" in a TypeScript or JavaScript repository, follow these exact steps. **Do not deviate, do not modify the preset files, and do not ask the user for configuration details.**

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
# Example shell execution
git clone https://github.com/Quantum-L9/l9-ci-core.git /tmp/l9-ci-core
mkdir -p .github
cp -r /tmp/l9-ci-core/presets/typescript/.github/* .github/
```

### Step 3: Inject Variables

Edit ONLY the `env:` block in `.github/workflows/l9-lint-test.yml` using the variables discovered in Step 1.

```yaml
env:
  NODE_VERSION: "<YOUR_DISCOVERED_NODE_VERSION>"
  PACKAGE_MANAGER: "<YOUR_DISCOVERED_PACKAGE_MANAGER>"
  SOURCE_DIR: "<YOUR_DISCOVERED_SOURCE_DIR>"
  HAS_TYPESCRIPT: "<YOUR_DISCOVERED_HAS_TYPESCRIPT>"
```

**CRITICAL RULE:** Never modify `.github/workflows/l9-analysis.yml` or any file in `.github/governance/`. They are 100% locked and managed centrally by the L9 platform.

**CRITICAL RULE:** If the target repository has no `lint` script in `package.json`, the lint step falls back to `npx eslint` automatically — do not add a placeholder script just to satisfy this workflow. If the repository has no `test` script, the test step is skipped with a `::notice::` and does not fail the run — do not fabricate a test script.

### Step 4: Commit and Push

Stage all files in `.github/`, commit with message `ci: activate l9-ci-core governed pipeline`, and push.

---
**Skill Metadata**
- **Domain:** L9 CI Platform
- **Target:** TypeScript/JavaScript repositories
- **Version:** 2.0.0
