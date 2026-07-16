# Validation report — l9-repo-preflight v2.1.0

Run on the skill package itself (no remote side effects were performed — per the
contract stop conditions, the skill was validated locally with git + a fake GitHub
adapter, never against a live repository).

## Commands & results

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check scripts/ tests/` | All checks passed! |
| Format | `ruff format --check scripts/ tests/` | 20 files already formatted |
| Probe syntax | `bash -n scripts/preflight_probe.sh` | OK |
| Tests | `pytest tests/` | ................................                                         [100%] |
| Exemplary gate | `python3 scripts/validate_exemplary_skill.py .` | PASS: exemplary skill validation passed |
| Type check | `mypy scripts/preflight/` | mypy not installed in this environment — deferred to CI (honest: unavailable here) |
| JSON schemas | Draft 2020-12 check on all schemas/*.json | all valid |
| YAML | parse expertise/intelligence yaml | all valid |
| SKILL.md links | relative-link resolution | 21/21 resolve |
| Package hygiene | no `__pycache__` / secrets in archive | enforced at packaging |

## Test coverage (mandatory suite)

- **unit**: failed_autofix_increments_blocker_count · successful_autofix_resolves_blocker ·
  missing_token_generates_sanitized_blocker · missing_token_creates/updates_issue ·
  duplicate_issue_not_created · duplicate_pr_not_created · no_applicable_action_not_a_blocker ·
  technical_debt_deduplicated · secrets_redacted · report_ordering_deterministic
- **integration**: successful_autofix_creates_branch_commit_push_and_pr ·
  failed_npm_ci_produces_blocked_final_status (simulated 401) · reports_written_to_docs_preflight ·
  ci_migration_isolated_in_separate_branch_and_pr · pr_monitoring_applies_bounded_repair ·
  external_blocker_stops_repair
- **regression**: eight_gates_preserved · allow_list_limits_autofix · dry_run_no_remote_side_effects ·
  direct_push_to_main_forbidden

## Unresolved unknowns / honest gaps

- **Live GitHub effects not exercised here.** `gh`-CLI is not installed and the stop
  conditions forbid remote side effects during skill validation. The `GhCliAdapter` is
  implemented and unit-tested via the fake adapter; a real PR/issue/monitor run requires
  `--enable-gh` in an authorized environment with credentials.
- **mypy not installed** in this environment; type hints are present and ruff passes.
- **npm ci 401** is validated as a *simulated* action outcome (no private registry available);
  the accounting + redaction + blocked-status path is fully exercised.

Total files in package: 38.
