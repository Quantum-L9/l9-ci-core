# l9-ci-core Makefile Component v4.1

Portable repository-command component for `Quantum-L9/l9-ci-core`, hardened against the donor-harvest acceptance tests.

## What it adds

- Delegation-only root `Makefile`, generated from `tools/l9_repo/Makefile.template`.
- Strict JSON configuration plus a closed JSON Schema.
- Safe `push` and `pr` behavior inherited from continuation v3.
- `make change-policy` for deterministic changed-file and companion-rule inspection.
- `make agent-check` as the mandatory non-source-mutating completion gate.
- Merge-base-aware committed-change detection, plus staged, unstaged, and untracked files.
- Ordered Core-specific gates and exact companion-change requirements.
- Collect-all, fail-final execution with exit taxonomy `0 / 1 / 2`.
- Deterministic JSON and Markdown evidence reports under ignored `artifacts/`.
- Remote freshness and ahead/behind diagnostics in `make status`.
- PID-aware single-flight mutation locking with safe stale recovery.
- Exact contract-to-agent wiring validation for Core authority files.

Targeted gates add diagnosis and proof. They never authorize skipping Core's complete test suite.

## Install

Overlay the component on a feature branch of a complete `l9-ci-core` checkout. Merge `AGENTS-ADDENDUM.md` into the existing `AGENTS.md`, regenerate `MANIFEST.sha256`, then run:

```bash
make setup
make validate
make agent-check
```

See `INTEGRATION.md`, `RUNBOOK.md`, and `PORT-COVERAGE.md` before merging.

No GitHub mutation is included in this pack.
