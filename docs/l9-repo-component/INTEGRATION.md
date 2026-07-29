# Integration Contract

1. Apply these files only to a feature branch of `Quantum-L9/l9-ci-core`.
2. Merge `AGENTS-ADDENDUM.md` into the existing root `AGENTS.md`; do not replace the existing file.
3. Preserve [`.l9/architecture.yaml`](.l9/architecture.yaml), [`.l9/ownership.yaml`](.l9/ownership.yaml), and [`.l9/sdk-compatibility.yaml`](.l9/sdk-compatibility.yaml) as Core authorities.
4. Do not import SDK-owned provider parsing, canonical findings/evidence, identity resolution, severity normalization, or policy classification.
5. Review the exact SDK-pin mirror list in `.l9/repo-workflow.json` against the target `AGENTS.md` before merge.
6. Keep `artifacts/` ignored so `make agent-check` does not dirty tracked source state.
7. Run `make reconcile` only when the root Makefile differs from `tools/l9_repo/Makefile.template`.
8. Run `make setup`, `make validate`, `make change-policy`, and `make agent-check`.
9. Regenerate `MANIFEST.sha256` after every accepted tracked change.
10. Keep CI workflow wiring in a separate PR until several real PRs show no selective-gate false negatives.
