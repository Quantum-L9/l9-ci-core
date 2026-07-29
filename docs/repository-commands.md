# Repository Commands

The root Makefile is a thin facade over `python -m tools.l9_repo`. It contains no policy logic.

## Completion gate

`make agent-check` is the mandatory completion proof. It:

1. verifies the supplied workspace is the repository root;
2. resolves committed changes from the merge-base with `origin/main` and unions staged, unstaged, and untracked files;
3. fails with exit `2` rather than treating missing change context as an empty clean set;
4. validates configuration, schema identity, Makefile/template parity, and exact agent-contract wiring;
5. runs configured validation once;
6. selects ordered Core-owned targeted gates;
7. enforces required companion prefixes and exact companion paths;
8. runs the complete lint/type gate once;
9. runs the complete repository test suite once;
10. continues after individual failures to expose the complete repair queue;
11. writes deterministic `artifacts/agent-check-evidence.json` and `.md` reports;
12. returns the final classified result.

The evidence output is an ignored diagnostic side effect. It does not modify tracked source files, apply fixes, commit, rebase, push, or open a pull request.

## Change policy

`make change-policy` prints the resolved change context, selected gates, and companion findings without executing validators.

Direct invocation supports controlled fixtures or automation:

```bash
python -m tools.l9_repo --workspace . \
  --base-ref origin/main \
  --head-ref HEAD \
  --changed-file path/to/file \
  change-policy
```

Explicit `--changed-file` values take precedence. Without explicit files, the engine uses the configured base comparison and working-tree changes.

## Status

`make status` performs a fetch-aware diagnostic and reports:

- branch and HEAD;
- dirty state;
- `remote_freshness: fresh` when fetch succeeds;
- `remote_freshness: unknown_offline` when fetch fails;
- the live or cached comparison ref;
- ahead and behind counts when a comparison ref exists;
- pull-request status when `gh` is available.

A failed fetch never becomes a false zero-divergence claim.

## Mutation commands

`make push` and `make pr` reject protected branches, detached HEAD, dirty trees, merge/rebase state, unsafe lock state, unresolved conflicts, stale lockfiles, and remote divergence unless safe rebase is explicitly configured. Both execute `agent-check` before mutation. No skip flag exists.

## Exit taxonomy

- `0`: all required checks passed.
- `1`: checks executed and one or more blocking findings failed.
- `2`: invalid configuration, missing change context, missing executable, unsafe repository state, or other infrastructure failure.
