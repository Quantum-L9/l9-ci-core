# Validation Report

## Executive decision

- **Port completeness:** `CONFIRMED_FOR_CURRENT_SCOPE`
- **Artifact validation status:** `BLOCKED_ON_TARGET_CHECKOUT_VALIDATION`
- **Candidate coverage:** every current `PORT`, `PORT_WITH_HARDENING`, and `MERGE_WITH_EXISTING` candidate from N-001 through N-011 is implemented and tested; N-010 remains explicitly deferred because no new external adapter is in Core scope.

The original v4 pack was not complete. This v4.1 pack repairs the identified gaps rather than treating them as documentation caveats.

## Remediated audit findings

1. **Committed changes were invisible on clean feature branches.** Fixed with merge-base comparison against the configured base plus staged, unstaged, and untracked union.
2. **Infrastructure failures could escape evidence collection or be flattened.** Fixed with collect-all command execution, explicit infrastructure classification, final exit `2`, and persisted evidence.
3. **Evidence output was JSON-only and omitted command output.** Fixed with deterministic JSON and Markdown reports containing bounded stdout/stderr.
4. **Remote freshness diagnostics were absent.** Fixed with fetch-aware live/cached status and honest offline labeling.
5. **Contract-to-agent discoverability was absent.** Fixed with exact Markdown-link/backtick validation for Core authority paths.
6. **Companion rules were too weak.** Fixed with exact all-path requirements for manifest integrity, command-facade docs/agents, and SDK pin mirrors.
7. **Compiled Python cache files leaked into the ZIP.** Removed and explicitly checked.

## Executed validation

| Check | Result | Evidence |
|---|---|---|
| Focused unit and synthetic repository tests | PASS, 54 tests | `validation/unit-tests.txt` |
| Python compilation | PASS | `validation/compileall.txt` |
| Draft 2020-12 JSON Schema validity | PASS | `validation/schema-validation.txt` |
| Config validates against schema and strict runtime validator | PASS | `validation/schema-validation.txt` |
| Makefile/template byte parity | PASS | `validation/static-scan.txt` |
| Unsafe execution primitive scan | PASS | `validation/static-scan.txt` |
| Makefile-level synthetic integration | PASS | `validation/synthetic-integration.txt` |
| JSON and Markdown evidence generation | PASS | `validation/synthetic-integration.txt` |
| Tracked worktree remains clean after `agent-check` | PASS | `validation/synthetic-integration.txt` |
| Port-candidate matrix | PASS for current scope | `PORT-COVERAGE.md` |
| Package contains no `.pyc` or `__pycache__` | PASS | package inspection and `validation/static-scan.txt` |

## Required after overlay into l9-ci-core

The standalone component intentionally does not duplicate Core's workflows, actions, complete tests, `requirements-ci.txt`, `uv.lock`, `AGENTS.md`, or `.l9` authority files. Overlay it into the pinned target checkout and run:

```bash
make setup
make validate
make change-policy
make agent-check
```

Then run the target's exact pinned quality tools and regenerate `MANIFEST.sha256`.

## Blocked checks

- Ruff `0.14.5` was unavailable in the offline container.
- Mypy `1.19.0` was unavailable in the offline container.
- A full GitHub clone could not be created because the container had no DNS access.
- Therefore, complete target-checkout regression remains mandatory before merge.

## Final recommendation

Use v4.1 instead of v4. The port is now complete for the donor-harvest scope, but do not label it merge-ready until the target-checkout commands above pass with Core's pinned toolchain.
