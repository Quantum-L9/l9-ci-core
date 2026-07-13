# RUNBOOK — CI enablement (l9-ci-core)

## Activate

1. **CodeRabbit** — install https://github.com/apps/coderabbitai on the org/repo.
   `.coderabbit.yaml` tunes it. No secret for the SaaS app.
2. **GitGuardian** — make `GITGUARDIAN_API_KEY` visible to this repo. Until then
   the job skips with a warning; once present it is blocking on same-repo PRs.
3. **Sonar** (org-level, no per-repo properties edits) — `pr-checks.yml` DERIVES
   `sonar.projectKey` as `<owner>_<name>` (SonarCloud's GitHub import convention,
   e.g. `Quantum-L9_l9-ci-core`) and DEFAULTS `sonar.organization` to `quantum-l9`.
   The only requirement is the org-level `SONAR_TOKEN` secret being visible to this
   repo. Optional org-level overrides: variable `SONAR_ORGANIZATION` (overrides the
   default org) and `SONAR_HOST_URL` (self-hosted only; defaults to
   `https://sonarcloud.io`). The real remaining unknown is whether the Sonar project
   is provisioned and the org-level token reaches this repo.
4. **Payload contract (real-payload lane)** — set the repo **variable**
   `L9_CI_INSTALL_SPEC` (e.g. `git+https://github.com/Quantum-L9/l9-ci-sdk.git@v0.1.0`)
   and make `SDK_TOKEN` visible so the SDK installs. Without it, only the
   schema meta-validation runs (still green).

## Make an advisory gate blocking

Drive findings to zero, then remove `continue-on-error: true` from that step in
`pr-checks.yml` (ruff is already clean; adopt a committed ruleset before making
it blocking so the default set can't surprise you).

## Payload handoff to PR_Repair

`pr-repair.yml` is manual (`workflow_dispatch`). It installs `l9-ci` via
`L9_CI_INSTALL_SPEC`, emits + schema-validates `agent_review_payload.json`, and
uploads it as `agent-review-payload`.

- `dispatch=true` **and** `L9_IMPLEMENTER_BOT_TOKEN` present → `repository_dispatch`
  (`event_type: l9-implementer-review`) to `Quantum-L9/PR_Repair`, which must
  expose a matching handler (its side).
- No token → dispatch skips with a notice.

The Implementer Bot and its LLM-Router / verify-rollback engine live in
`Quantum-L9/PR_Repair`, not here. This repo owns the schema and emits the
payload. Never merge, never push, never change settings from CI.

## Fork PRs

Secret-dependent jobs are same-repo only. Never switch to `pull_request_target`.
