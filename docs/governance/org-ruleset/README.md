# Org-Level Ruleset — Draft (not yet applied)

Phase 4 of the governance model: publish an org-level required-status-checks
ruleset so the L9 CI pipeline actually gates merges, not just reports.

**Status: drafted, not applied.** Nothing in this directory has been sent to
the GitHub API. `l9-required-checks.ruleset.json` is a reviewable draft body
for `POST /orgs/Quantum-L9/rulesets`.

## Decisions locked in for this draft (2026-07-23)

| Decision | Choice |
|---|---|
| Required checks | `Lint and Type Check`, `Test Suite`, `Governed Semgrep Analysis` |
| Enforcement mode | `evaluate` (dry-run — logs would-be blocks, does not actually block) |
| Scope | The 28 repos with the l9-ci-core preset activated (24 rollout PRs + 4 pre-existing) |
| Gap repos (11 with zero L9 CI) | Excluded from this draft — onboard them to the preset first, then add to scope |

## Why `evaluate`, not `active`, and why scope is capped at 28 repos

As of 2026-07-23, `Lint and Type Check` fails on 22 of the 24 newly-activated
repos (pre-existing code debt the preset surfaced, not a CI bug), and several
also fail `Test Suite` / `SonarCloud Code Analysis`. Flipping `active` today
would immediately block merges — including on these repos' own currently-open
PRs. `evaluate` mode gives visibility (GitHub's Rule Insights page) without
blocking anything, so debt can be triaged with real data before enforcement
bites.

Run `tools/apply_org_ruleset.py preview` at any time to see current
pass/fail state per repo against the required-check list — this is the same
signal `evaluate` mode would surface, computed locally via `gh` against
`check-runs` on the tip of `main` (not `gh run list`, which returns workflow
names rather than the individual check-run contexts that
`required_status_checks` actually matches against).

**Ran 2026-07-23: 28/28 repos currently show `WOULD_BLOCK`.** Read this
number carefully — it is not one uniform failure mode:

- For the **24 not-yet-merged rollout repos**, this is expected: the preset's
  checks don't exist on `main` at all until the activation PR merges. This is
  not a CI failure, it's an unmerged-PR artifact.
- For the **4 pre-existing repos**, it's a mix: `Cursor-Governance` and
  `Enrichment.Inference.Engine` have real `Lint and Type Check` / `Test
  Suite` failures on `main`; `l9-assurance` fails `Governed Semgrep
  Analysis`; `LLM-Router` doesn't run the standardized preset job names at
  all (it predates this preset and has its own custom pipeline —
  `ESLint`/`Vitest`/`tsc --noEmit` instead of `Lint and Type
  Check`/`Test Suite`), so it would need re-onboarding onto the actual preset
  to ever satisfy this ruleset, not just a debt fix.

Re-run `preview` after merging the 24 PRs to get a real signal on remaining
debt — right now the number is dominated by "not merged yet," not "broken."

## Known pre-existing landmine

There is a **repo-level** ruleset called `"CI Gate"` (seen on at least
`Cognitive.Engine.Graphs`) with `conditions.ref_name.include: []` — an empty
include array. GitHub's GET returns it fine, but the create/update API
rejects an empty include list. Any new ruleset must use `~DEFAULT_BRANCH` or
`~ALL`, never `[]`. This draft already does that correctly.

## The 11 repos with zero L9 CI coverage (excluded from this draft)

- `.github`, `l9-repo-template` — not consumer repos, no action needed.
- `l9-ci-core`, `l9-ci-sdk` — source-of-truth repos, use self-CI (`self-ci.yml`)
  with different check names; would need their own ruleset entry, not this one.
- `Governance-Active`, `Quantum-Website-Cursor`, `SplitWisely.ai`,
  `SustainabilitySolutions1`, `ai-agency-genesis-portal`, `l9-infra`,
  `quantum-dashboard` — real product repos with no L9 CI at all. Per the
  "onboard first" decision, these need the preset activated (same rollout
  mechanism as the 24 repos, see `presets/python/README.md` or
  `presets/typescript/README.md`) and driven green *before* being added to
  `conditions.repository_name.include` in the ruleset JSON.

## There is already an empty org ruleset

`orgs/Quantum-L9/rulesets` currently returns one existing ruleset:
`"Quantum AI Policy"` (id `18226001`), `target: "repository"`,
`enforcement: "disabled"`, `rules: []`. It predates this work and does
nothing. Decide whether to repurpose it (`tools/apply_org_ruleset.py update
18226001 --confirm`) or create a separate one
(`tools/apply_org_ruleset.py create --confirm`) — the draft JSON here targets
`"branch"`, not `"repository"`, so repurposing 18226001 would also change its
`target`, which the API allows on update.

## How to actually activate, when ready

```bash
# 1. See current state (safe, no mutation)
python3 tools/apply_org_ruleset.py preview

# 2. Dry-run the exact API payload (safe, no mutation)
python3 tools/apply_org_ruleset.py create

# 3. Actually create it in evaluate mode (mutates org settings)
python3 tools/apply_org_ruleset.py create --confirm

# 4. After a burn-in period, once required checks are green across all
#    scoped repos, flip enforcement to "active" by editing
#    l9-required-checks.ruleset.json and re-running update/create --confirm.
```

## Next steps (not yet done)

1. Onboard the 6-7 gap product repos to the preset (reuse the rollout script
   pattern used for the original 24).
2. Triage `Lint and Type Check` / `Test Suite` / `SonarCloud` failures across
   the 24 newly-activated repos — fix or explicitly waive.
3. Run `apply_org_ruleset.py create --confirm` to publish the `evaluate`-mode
   ruleset and start collecting Rule Insights data.
4. Once green, flip `enforcement` to `active` and widen
   `repository_name.include` to cover onboarded repos.
