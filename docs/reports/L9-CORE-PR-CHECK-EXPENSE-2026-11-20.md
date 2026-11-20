# Core pull-request check expense (2026-11-20)

**Verdict: no Core pull-request check is heavy enough to move to the nightly
path.** Every GitHub Actions job has a median under 1.2 minutes. The slowest
check is the external Semgrep app, at a median of 2.0 minutes. `Analyze
(central Core)` stays on pull requests, as required.

## Method

- Source: REST `GET` only, on `Quantum-L9/l9-ci-core`.
- Sample: the last 10 completed (success or failure, not cancelled)
  `pull_request` runs of each active workflow.
- Duration: job `started_at` to `completed_at`.
- Queue: run `created_at` to job `started_at`.
- CodeQL default setup reports event `dynamic`, so it was measured from the
  check runs on the same PR head commits.
- External apps were measured from their check runs on 12 PR heads.
- Window: 2026-11-20, 17:43 to 19:11 UTC. That shows current behaviour, not
  variation across days.

## Results, by median (descending)

| Check | Source | Required | n | Median min | Max min | Classification |
|---|---|---|---|---|---|---|
| semgrep-cloud-platform/scan | external app | no | 12 | 1.97 | 3.02 | keep; not Actions-controlled |
| Analyze (central Core) | `org-ci.yml` | no | 10 | 1.17 | 1.27 | **keep on PR (directed)** |
| Analyze (python), code scanning | CodeQL default setup | no | 8 | 1.02 | 1.10 | keep; repository setting, not a workflow |
| SonarQube Cloud / SonarQube Cloud | `self-ci.yml` | no | 4 | 0.92 | 0.93 | keep |
| Analyze (python), code quality | CodeQL code quality | no | 8 | 0.88 | 1.12 | keep; repository setting |
| SonarQube Cloud | `self-sonar.yml` | no | 10 | 0.88 | 1.08 | keep |
| Analyze (semgrep) / Analyze (semgrep -> SDK) | `self-analysis.yml` | **yes** | 10 | 0.83 | 0.88 | keep (required) |
| Analyze (actions) | CodeQL default setup | no | 8 | 0.69 | 0.78 | keep |
| SonarCloud Code Analysis | external app | no | 11 | 0.55 | 0.68 | keep |
| Unit tests | `self-ci.yml` | **yes** | 10 | 0.55 | 0.98 | keep (required) |
| sdk-contract | `sdk-contract-check.yml` | **yes** | 10 | 0.43 | 0.52 | keep (required) |
| Publish terminal analysis check | `self-analysis.yml` | no | 10 | 0.39 | 0.48 | keep |
| Repository contract | `self-ci.yml` | **yes** | 10 | 0.36 | 0.42 | keep (required) |
| Security scan / security | `self-security.yml` | **yes** | 10 | 0.28 | 0.32 | keep (required) |
| Lint and Type Check | `self-ci.yml` | **yes** | 10 | 0.23 | 0.23 | keep (required) |
| Governance validate | `governance-ci.yml` | **yes** | 10 | 0.08 | 0.13 | keep (required) |

The median queue wait is 0.05 minutes for nearly every Actions job. The
exceptions are `Analyze (central Core)` at 0.53 minutes, SonarQube Cloud in
`self-ci.yml` at 0.92 minutes, and the publish check, which waits for
Semgrep.

## Why nothing moves

- The heavy work already runs only at night. The full `unittest discover`
  suite (`make test-deep`, 1235 tests in about 38 s locally) runs only in
  `nightly-deep.yml`. Pull requests run the explicit module lists, and
  `org-ci.yml` refuses a full-suite fallback (`PR_GUARD_PYTHON`).
- Seven checks are required by
  [`docs/release/rulesets/core-main-protection.json`](../release/rulesets/core-main-protection.json).
  Moving one would be a ruleset change, which this lane may not make.
- CodeQL default setup and code quality are repository settings, not
  workflows. Moving them is a settings change, out of this lane's scope.
- The Semgrep app is a third-party check, not controlled from Actions.

## Watch item

SonarCloud Code Analysis (the external app) concluded `failure` on 9 of 11
samples. That is a correctness signal, not an expense one. It is reported
here and not acted on.

## Re-measure

Repeat this when a PR job's median passes 5 minutes, or when a new heavy
job is added. The sampled run ids are listed in the lane's PR.
