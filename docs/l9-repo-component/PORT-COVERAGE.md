# Donor Port Coverage

Baseline: `DONOR-HARVEST-BRIEF-l9-ci-core-20260729.md`.

| ID | Candidate | v4.1 disposition | Implementation evidence | Acceptance evidence |
|---|---|---|---|---|
| N-001 | Declarative change-impact contract | **PORTED_WITH_HARDENING** | `.l9/repo-workflow.json`, schema, `change_policy.py` | Clean committed feature diff, unioned worktree changes, exact mirrors, companions, missing-context tests |
| N-002 | Collect-all, fail-final execution | **PORTED_WITH_HARDENING** | `RepositoryWorkflow.agent_check` | Multiple findings and missing-executable tests prove later steps still run |
| N-003 | Single non-mutating agent completion command | **MERGED** | Makefile target and `agent_check` | Tracked worktree remains clean; push has no bypass path |
| N-004 | Evidence-bearing check report | **PORTED_WITH_HARDENING** | `reporting.py` JSON and Markdown renderers | Determinism and stdout/stderr evidence tests |
| N-005 | Exit-code taxonomy | **PORTED** | CLI exception mapping and agent-check final classification | Findings exit `1`; missing context/executable/state exit `2` |
| N-006 | Workspace-targeted facade | **MERGED** | `WS` equivalent via `--workspace` and Makefile `CURDIR` | Non-root and non-Git workspace tests |
| N-007 | Policy / engine / rendering separation | **MERGED** | JSON policy, Python engine, generated Makefile | Makefile/template parity and no embedded policy logic |
| N-008 | Freshness and divergence diagnostics | **PORTED_WITH_HARDENING** | `RepositoryWorkflow.status` | Live and offline/cached status tests |
| N-009 | Single-flight mutation ownership | **MERGED** | `locking.py` | Live owner, dead owner, and unexpected-content tests |
| N-010 | Structural adapter validator pattern | **DEFERRED BY DESIGN** | No new external agent adapter exists in Core | Trigger remains future adapter introduction; not a current Core port candidate |
| N-011 | Contract-to-agent wiring proof | **PORTED_WITH_HARDENING** | `contract_wiring.py` plus `agent_contracts` config | Exact Markdown/code path tests; loose prose and missing files fail |

## Explicitly rejected donor behavior

- Mutating validation or autofix inside `agent-check`.
- Infrastructure failures converted to pass.
- Hard-coded donor credentials.
- No-context change policy returning success.
- Advisory `|| echo` gate suppression.
- Push-check bypass flags or dirty-tree push.
- Shell command strings, `shlex`, `shell=True`, `eval`, or `os.system`.
- Feature-branch `HEAD` pushed directly to `main`.
- Timestamp-only stale-lock breaking.
- Claims of local/CI parity without an executable, tested path.

## Scope note

N-010 was classified by the harvest as a future configured pattern, not a Core-main implementation requirement. Every current `PORT`, `PORT_WITH_HARDENING`, and `MERGE_WITH_EXISTING` candidate is represented in v4.1.
