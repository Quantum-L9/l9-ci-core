# Validation Report

## Decision

```yaml
authority_alignment: PASS
internal_execution: PASS
release_status: APPROVED_WITH_TARGET_CHECKOUT_GATE
```

The v4.3.0 pack is a condensed authority release. Executable policy now owns release identity, precedence, dependency manifests, generated artifacts, and the list of derived documents. Redundant v4.2 audit, port-coverage, command-reference, integration, and runbook documents were removed after their unique content was consolidated.

## Improvements validated

- Added machine-validated artifact identity and semantic version.
- Added an explicit authority order that preserves target Core law above component policy.
- Added derived-document and dependency-manifest declarations.
- Added validation that README, authority contract, and manifest agree with executable identity.
- Replaced embedded setup-package versions with `requirements-repo-runtime.txt` while retaining target-owned `requirements-ci.txt`.
- Removed the unused pytest installation from component setup.
- Consolidated integration, command, evidence, failure, and recovery guidance into `OPERATIONS.md`.
- Reduced overlapping human documents from nine to six authority/operator/evidence documents.
- Corrected the stale v4.1 identity previously present in `MANIFEST.md`.

## Executed checks

| Check | Result | Evidence |
|---|---|---|
| Unit and adversarial suite | PASS, 67 tests | `validation/unit-tests.txt` |
| Python compilation | PASS | `validation/compileall.txt` |
| Draft 2020-12 schema and instance | PASS | `validation/schema-validation.txt` |
| Strict runtime configuration | PASS | `validation/config-validation.txt` |
| Synthetic Core checkout plus `agent-check` | PASS | `validation/synthetic-integration.txt` |
| Unsafe execution primitive scan | PASS | `validation/static-scan.txt` |
| Makefile/template parity | PASS through structural validation | synthetic and unit evidence |
| Identity and derived-document drift rejection | PASS | adversarial unit evidence |
| Dependency-manifest absence rejection | PASS | adversarial unit evidence |

## Release inventory

- Release files outside validation logs: 28
- Runtime tests: 67
- Human authority and operator surfaces: `README.md`, `AUTHORITY.md`, `OPERATIONS.md`, `AGENTS-ADDENDUM.md`, `MANIFEST.md`, `VALIDATION.md`
- Executable authority: `.l9/repo-workflow.json`

## External gate

This component intentionally omits the target repository's complete workflows, actions, lockfile, authority files, and full test corpus. Overlay it onto the current `Quantum-L9/l9-ci-core` feature branch and run:

```bash
make setup
make validate
make change-policy
make agent-check
uv lock --check
```

Then regenerate the target repository's `MANIFEST.sha256` and rerun the complete pinned Core quality surface.

## Convergence

No remaining internal contradiction was found between release identity, executable policy, schema, dependency ownership, generated facade, operator guidance, test evidence, and manifest inventory. Production merge readiness remains conditional only on the complete target-checkout gate.
