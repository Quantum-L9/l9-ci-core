# MANIFEST — CI enablement (l9-ci-core)

Adapted from the `l9-ci-enablement-pack` (model repo: `Quantum-L9/PR_Repair`).
Re-derived for this repo's actual stack; nothing blind-copied.

## Files

| Path | Responsibility | Consumes | Blocking? |
|---|---|---|---|
| `.github/workflows/pr-checks.yml` | PR quality + security gate | `GITGUARDIAN_API_KEY`, `SONAR_TOKEN` | pytest **blocking**; GitGuardian blocking *when secret present*; ruff/mypy/Sonar advisory |
| `.github/workflows/agent-payload-contract.yml` | Validate the payload schema; generate+validate a real payload when the SDK is installable | var `L9_CI_INSTALL_SPEC`, `SDK_TOKEN` | schema meta-validation **blocking** (always green); real-payload step guarded |
| `.github/workflows/pr-repair.yml` | Payload source + handoff to PR_Repair (no bot vendored) | var `L9_CI_INSTALL_SPEC`, `SDK_TOKEN`, `L9_IMPLEMENTER_BOT_TOKEN` (optional) | n/a (manual dispatch) |
| `.coderabbit.yaml` | CodeRabbit review tuning | app install | n/a |
| `sonar-project.properties` | Sonar mapping (`.github/scripts`) + coverage | `SONAR_TOKEN` | n/a |
| `AGENT.md` | Governance contract the Implementer loads | — | — |
| `docs/ci-enablement/*` | This pack's docs | — | — |

Note: the reusable `pr-pipeline.yml` already emits and uploads
`agent_review_payload.json`; `agent-payload-contract.yml` adds the schema
validation that was missing.

## Secret / variable map (visibility on private repos must be confirmed)

| Name | Kind | Used by |
|---|---|---|
| `GITGUARDIAN_API_KEY` | secret | pr-checks / gitguardian |
| `SONAR_TOKEN` | secret | pr-checks / sonar |
| `SDK_TOKEN` | secret | install private `l9-ci` |
| `L9_CI_INSTALL_SPEC` | variable | pip spec for `l9-ci` (e.g. `git+https://github.com/Quantum-L9/l9-ci-sdk.git@v0.1.0`) |
| `L9_IMPLEMENTER_BOT_TOKEN` | secret (optional) | cross-repo dispatch to PR_Repair |

## Unknowns (must be filled — not invented)

| Where | Value | How to resolve |
|---|---|---|
| Sonar (org-level) | project provisioned? org `SONAR_TOKEN` visible? | `sonar.projectKey` is DERIVED (`<owner>_<name>`) and `sonar.organization` DEFAULTS to `quantum-l9` (override via `SONAR_ORGANIZATION`); provision the SonarCloud project and confirm the org token reaches this repo |
| repo/var settings | `L9_CI_INSTALL_SPEC` + `SDK_TOKEN` visibility | set the install spec; confirm the token reaches this private repo |
| PR_Repair | `on: repository_dispatch` handler for `l9-implementer-review` | wired on the PR_Repair side |
