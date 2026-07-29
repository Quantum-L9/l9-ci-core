# Operations

## Prerequisites

Use a complete `Quantum-L9/l9-ci-core` feature-branch checkout with Python, Git, `uv`, and GitHub CLI available. `requirements-ci.txt` remains target-owned; `requirements-repo-runtime.txt` owns only this component's additional dependencies. Read the target `AGENTS.md` and its referenced `.l9` authority files before applying the component.

## Integration sequence

1. Overlay the pack without replacing target authority files.
2. Merge `AGENTS-ADDENDUM.md` into the target `AGENTS.md`.
3. Keep `artifacts/` ignored.
4. Regenerate `MANIFEST.sha256` after accepted tracked changes.
5. Run:

```bash
make setup
make validate
make change-policy
make agent-check
uv lock --check
```

6. Run the target repository's pinned quality surface and inspect both evidence reports.

## Command contract

- `make validate`: schema, checksum manifest, generated-facade parity, authority identity, and target-contract wiring.
- `make change-policy`: changed-file resolution, selected targeted gates, and companion obligations.
- `make agent-check`: structural validation, targeted gates, complete check/test suites, non-mutation proof, and JSON/Markdown receipt generation.
- `make status`: branch, worktree, upstream, ahead/behind, and freshness diagnostics.
- `make push` / `make pr`: single-flight guarded mutation after a passing completion proof.
- `make reconcile`: regenerate the root Makefile from its template.
- `make clean`: remove only configured cache paths.

## Evidence

The completion receipt is written to:

- `artifacts/agent-check-evidence.json`
- `artifacts/agent-check-evidence.md`

The receipt records the initial subject and policy digest, change context, selected gates, companion findings, every executed command, bounded redacted output, and the final classification.

## Failure and recovery

- Exit `1`: fix all reported blocking findings and rerun.
- Exit `2`: restore valid configuration, tools, comparison context, authority wiring, manifest integrity, or repository state, then rerun.
- Lock busy: confirm the recorded process before removing a stale lock; never delete a live lock.
- Unknown or unavailable remote state: fetch or restore a valid upstream before mutation.
- Makefile drift: run `make reconcile`, inspect the diff, regenerate `MANIFEST.sha256`, and rerun validation.
