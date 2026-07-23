# Skill: L9 CI Activation

## Purpose
This skill provides the exact deterministic protocol for an AI agent to activate L9 CI in any Python repository. It uses the locked `python` preset from `Quantum-L9/l9-ci-core` to ensure zero-friction, zero-ambiguity setup.

## Capabilities
- Provides the 3-step protocol for enabling governed L9 CI in a Python repo
- Defines how to discover the two repo-specific variables (`SOURCE_DIR`, `TEST_DIR`)
- Establishes the exact copy commands to pull the locked preset

## Instructions for Agent

When tasked to "activate CI", "setup CI", or "enable l9-ci-core" in a Python repository, follow these exact steps. **Do not deviate, do not modify the preset files, and do not ask the user for configuration details.**

### Step 1: Discover Repository Variables
Inspect the target repository to determine these two values:

1. **`SOURCE_DIR`**: 
   - Look at `pyproject.toml` or directory structure.
   - If `src/` exists, set `SOURCE_DIR="src/"`.
   - Otherwise, set `SOURCE_DIR="."`.

2. **`TEST_DIR`**:
   - If `tests/` exists, set `TEST_DIR="tests/"`.
   - If `test/` exists, set `TEST_DIR="test/"`.
   - Otherwise, set `TEST_DIR="tests/"`.

### Step 2: Copy the Locked Preset
Clone `Quantum-L9/l9-ci-core` to a temporary directory, then copy the entire `presets/python/.github` folder into the target repository root.

```bash
# Example shell execution
git clone https://github.com/Quantum-L9/l9-ci-core.git /tmp/l9-ci-core
mkdir -p .github
cp -r /tmp/l9-ci-core/presets/python/.github/* .github/
```

### Step 3: Inject Variables
Edit ONLY the `env:` block in `.github/workflows/l9-lint-test.yml` using the variables discovered in Step 1.

```yaml
env:
  PYTHON_VERSION: "3.12"
  SOURCE_DIR: "<YOUR_DISCOVERED_SOURCE_DIR>"
  TEST_DIR: "<YOUR_DISCOVERED_TEST_DIR>"
  COVERAGE_THRESHOLD: "0"
```

**CRITICAL RULE:** Never modify `.github/workflows/l9-analysis.yml` or any file in `.github/governance/`. They are 100% locked and managed centrally by the L9 platform.

### Step 4: Commit and Push
Stage all files in `.github/`, commit with message `ci: activate l9-ci-core governed pipeline`, and push.

---
**Skill Metadata**
- **Domain:** L9 CI Platform
- **Target:** Python repositories
- **Version:** 2.0.0
