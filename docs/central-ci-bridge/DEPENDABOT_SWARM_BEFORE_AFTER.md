# Dependabot swarm — before / after

Measured 2026-09-02. **No pull request has been closed, merged, or modified.**
This is the classification and the projected effect of the migration.

## Before (measured)

Open Dependabot pull requests, and the subset whose head branch targets a
first-party `Quantum-L9/l9-ci-core` or `Quantum-L9/l9-ci-sdk` reference:

| Repository | Open PRs | Dependabot | First-party CI pins | Real dependencies |
|---|---|---|---|---|
| `l9-ci-debt-intelligence` | 12 | 10 | **4** | 6 |
| `l9-ci-debt-lsp` | 8 | 7 | **5** | 2 |
| `l9-ci-debt-resolver` | 7 | 6 | **5** | 1 |
| `Cursor-Governance` | 17 | 5 | **5** | 0 |
| `l9-ci-sdk` | 5 | 2 | 0 | 2 |
| `l9-repo-template` | 1 | 1 | 0 | 1 |
| `l9-ci-core` | 1 | 0 | 0 | 0 |
| **Total** | | **31** | **19** | **12** |

**19 of 31 open Dependabot PRs (61%) exist only because consumers own
first-party organization-CI pins.**

## The mechanism

Each consumer pins Core's internal actions by 40-hex SHA inside its own
`l9-analysis.yml`. Dependabot's `github-actions` ecosystem treats every
`uses:` as an independent dependency, so **one** Core commit fans out to
*(number of distinct Core actions referenced)* × *(number of consumers)* pull
requests.

Observed branch shapes for a single Core bump to `aaa0112`:

```
dependabot/github_actions/…/l9-ci-core/dot-github/actions/resolve-governance-aaa0112…
dependabot/github_actions/…/l9-ci-core/dot-github/actions/provision-sdk-aaa0112…
dependabot/github_actions/…/l9-ci-core/dot-github/actions/validate-bundle-aaa0112…
dependabot/github_actions/…/l9-ci-core/dot-github/workflows/publish-analysis.yml-aaa0112…
```

Each of those PRs also triggers the full consumer workflow set — the run
listings show `Organization CI (Core)` plus 6–9 phase workflows per PR — so the
swarm costs CI minutes proportional to *pins × consumers*, not to real risk.

The pins are pure churn: they select a Core revision that, after migration, the
**organization ruleset** selects instead. A consumer has no authority to choose
the Core revision that enforces organization policy, so a PR asking it to do so
is answering a question the consumer is not allowed to answer.

## After (projected — not yet executed)

Once a consumer's `l9-analysis.yml` and its copied governance pack are removed,
the `uses:` edges vanish and Dependabot cannot rediscover them: there is no
manifest left to scan. Central CI reaches the repository through the org
ruleset, whose Core revision is control-plane state, not repository content.

| Class | Before | After | Mechanism |
|---|---|---|---|
| First-party Core/SDK org-CI pins | **19** | **0** | dependency edge deleted with the caller |
| Real application/runtime dependencies | 12 | 12 | unchanged — still watched |
| **Total open Dependabot PRs** | 31 | 12 | −61% |

Nothing here disables Dependabot, adds a broad `ignore` rule, or hides a real
dependency. The reduction comes solely from deleting dependency edges that
should not exist.

## Handling the 19 obsolete PRs

**Not yet actionable.** Per the contract, an obsolete pin PR may be closed only
once the underlying dependency edge no longer exists. That requires consumer
migration, which is gated on central CI going green
(`CENTRAL_CI_BRIDGE_FINDINGS.md` §7).

Order of operations, once unblocked:

1. Get `Organization CI (Core)` green on ≥ 2 canaries.
2. Delete the consumer caller + copied governance (identity map promoted
   centrally **first** — see the migration matrix).
3. *Then* close the now-obsolete pin PRs as superseded, referencing the
   migration PR.
4. Confirm the next Dependabot scan does not recreate them.

**Do not merge these PRs.** Merging a Core pin bump into a consumer entrenches
exactly the ownership inversion being removed, and each merge re-triggers the
full consumer workflow set.

## What is explicitly out of scope

* Disabling Dependabot anywhere.
* Blanket `ignore` rules for `github-actions`.
* Moving application/runtime dependency versions into Core.
* Auto-merging dependency majors.

The 12 real dependency PRs keep their current review posture: minor/patch may be
grouped where risk is homogeneous, majors stay separate and human-reviewed,
security updates stay expedited.
