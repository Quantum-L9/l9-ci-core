"""l9-repo-preflight delivery/accounting layer (v2.1.0).

Separated concerns (each independently testable):
  accounting   — classify autofix outcomes; failed applicable autofix => unresolved blocker
  techdebt     — evidence-based technical-debt detection
  reports      — persist deterministic, redacted reports under docs/preflight/
  delivery     — git branch/commit/push + PR creation (via a replaceable adapter)
  issues       — GitHub issue sync (dedupe/create/update) (via the adapter)
  monitor      — bounded PR-repair monitoring with terminal states
  ci_migration — generate the thin reusable-workflow CI caller
  github        — the provider adapter: GhCliAdapter (real) + DryRunAdapter (safe/tests)

All remote side effects go through the adapter and are idempotent + dry-run-able;
nothing here fabricates a remote action — every effect returns a captured receipt.
"""

__version__ = "2.1.0"
