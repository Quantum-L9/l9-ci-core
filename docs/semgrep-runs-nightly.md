# Semgrep runs nightly, not on pull requests: dated amendment of row 23 (Semgrep)

This is a decision record, kept outside `docs/adr/` like
[`central-diff-baseline.md`](central-diff-baseline.md): that directory holds
only Core's proposed, invariant-binding ADRs.

## Status

Accepted 2026-11-21. Operator decision, verbatim: "the semgrep scan inside
analyze must be removed and the semgrep check moved to nightly". It
**supersedes row 23 for Semgrep**: Semgrep no longer judges pull requests,
merge groups or pushes, so the CENTRAL-DIFF baseline that row 23 set for
Semgrep on those events no longer applies anywhere. Row 23 is unchanged for
every check that still judges a change (repository verification, Biome, the
security scan's event-scoped secret scan and dependency audit).

**Decision owner:** `Quantum-L9/l9-ci-core`. **Related:**
[`central-diff-baseline.md`](central-diff-baseline.md) (row 23),
[`nightly-findings-sink.md`](nightly-findings-sink.md) (row 24, X4),
[`blocking-check-classification.md`](blocking-check-classification.md),
AGENTS.md §40 (promotion canary), L9-CI-CORE-003.

## Decision

| Where | Before | After |
|---|---|---|
| `org-ci.yml` `analyze` (the required check, `.l9/org-runtime-contract.yaml` `entrypoint.required_check.name`) | repository verification, Biome, security scan **and** the Semgrep chain: SDK provisioning, language detection, inventory and plan, CENTRAL-DIFF baseline, clean scan tree, Semgrep run, SDK gate, every planned instance, aggregate, artifacts and handoff, and "Enforce central mode on SDK technical gate" | repository verification, Biome, toolchain alignment and the security scan only. Its verdict cannot depend on Semgrep |
| `central-semgrep.yml` | (did not exist) | the one source of the Semgrep chain. `workflow_call` only and called by nothing, so it never runs on a pull request. A clean checkout, the whole tree, no baseline, no repository verification first, so no scan tree |
| `nightly-sink.yml` | rendered from org-ci.yml's analyze steps (verification, Biome advisory, Semgrep) | rendered from `central-semgrep.yml`: Semgrep only, every planned instance including the consumer rulepack (`.l9/semgrep-consumer-rules.yaml`), each repository's default branch, every night. Findings go to code scanning and the rolling issue (row 24) |
| Promotion canary | head and base legs of org-ci.yml (with Semgrep), admitted-SDK leg | org-ci.yml head and base legs (Semgrep-free) **plus** nightly-scan legs `scan` and `scanbase` rendered from `central-semgrep.yml`; the admitted-SDK leg is a scan leg (the scan is the only family that provisions the SDK). Scan legs run as a `nightly` event like the sink (whole tree, advisory), so both sides are the same kind of run: a leg that cannot conclude, or a finding the pull request adds, fails. The verdict judges both tables, so a Semgrep change is proven before promotion |

Kept unchanged: org-ci.yml's runtime resolution and its `workflow_call` /
`workflow_dispatch` inputs (`sdk-revision`, `semgrep-version` are accepted
and unused, so no caller breaks). Its `workflow_call` outputs `gate-status`,
`artifact-name`, `artifact-handoff` and `sdk-revision` moved to
`central-semgrep.yml`, the workflow that produces them.

## Consequences

- A Semgrep finding never blocks a merge. It reaches the repository as a code
  scanning alert and a line of its "Nightly findings" issue, where the nightly
  ratchet fails the night when the open count rises.
- The required check no longer installs Python, Semgrep or the SDK, so it
  is shorter by the whole Semgrep chain. The measured before/after on one
  consumer is reported on the pull request that made this change.
- The scan tree and the CENTRAL-DIFF baseline steps are gone with Semgrep, and
  so are their tests (`test_org_ci_scan_tree.py`,
  `test_org_ci_central_diff_baseline.py`). The instance-runner tests
  (`test_central_semgrep_required_instances.py`,
  `test_instance_runner_scan_root_paths.py`) run against `central-semgrep.yml`
  and every copy rendered from it.
- `org-ci.yml`'s manual `release` and `supply_chain` dispatch classes no longer
  run Semgrep either; a release that must be Semgrep-judged calls
  `central-semgrep.yml` with `event: release` (strict, identity map staged).
- L9-CI-CORE-003 (every applicable instance planned, run and accounted for)
  now binds the nightly scan, where Semgrep's instances run.
