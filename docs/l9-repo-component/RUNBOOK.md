# Runbook

## Prerequisites

- A complete `Quantum-L9/l9-ci-core` checkout at the repository root.
- Python 3.12 or newer.
- Git and GitHub CLI.
- The Core CI dependencies installed by `make setup`.
- A cached `origin/main` ref, or explicit changed files/base ref for policy inspection.

## Normal sequence

```bash
make doctor
make setup
make validate
make change-policy
make agent-check
```

`make agent-check` always retains the full test suite. Targeted gates are additional diagnostics.

## Evidence

Successful or failed agent checks write:

```text
artifacts/agent-check-evidence.json
artifacts/agent-check-evidence.md
```

The report includes change context, changed files, companion findings, every executed command, classification, exit code, and bounded stdout/stderr.

## Common failures

### Exit 1

The validation infrastructure ran and found blocking debt. Read the companion findings and failed steps in the evidence report, fix all reported problems, then rerun.

### Exit 2: comparison ref unavailable

Fetch the target base or provide an explicit base:

```bash
git fetch origin main
python -m tools.l9_repo --workspace . --base-ref origin/main change-policy
```

### Exit 2: authoritative path not referenced

Restore the exact Markdown link or backticked path in `AGENTS.md`. A loose filename mention does not satisfy wiring proof.

### Exit 2: missing executable

Run `make setup` and verify `make doctor`. Infrastructure errors are never converted into a passing validation result.

### Lock busy

Another mutation owns the Git-directory lock. Do not delete it while the recorded PID is alive. A dead stale owner is reclaimed only when the lock directory contains the expected owner marker and nothing else.

### Status says `unknown_offline`

Ahead/behind values, when present, came from cached refs. Restore connectivity and rerun `make status` before making divergence-sensitive decisions.

## Recovery

- `make reconcile` restores the generated Makefile from the canonical template.
- Never use a bypass flag; none is supported.
- Never push a feature branch directly into `main` through a rewritten refspec.
- Revert the component commit if selective gates miss a full-suite failure during rollout.
