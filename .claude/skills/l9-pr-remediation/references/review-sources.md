<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: review_sources
tags: [pr, review, sources, sonarcloud, snyk, semgrep, sarif, check-runs, secrets]
owner: igor_beylin
status: active
version: 3.3.1
updated: 2026-07-13
/L9_META -->

# Review Sources Registry

## Purpose

Read review feedback from **every** tool watching a PR, not just what renders on the PR page. The GitHub PR surface shows a tool's *summary comment + gate status*; the **full detailed findings live in the tool's own API or in GitHub's check/SARIF layers**. This registry makes each source a data entry with a uniform fetch → normalize contract, so validation, confidence-gating, and `bot_false_positive_rate` self-tuning work per-source automatically.

## Two tiers

### Tier 1 — GitHub-native (always on, one `GITHUB_TOKEN`; no vendor keys)

| Source id | type | Fetch | Reads |
|-----------|------|-------|-------|
| `github_comments` | `github_comments` | `/pulls/{n}/reviews`, `/pulls/{n}/comments` *(already in signal-ingestion.md)* | CodeRabbit, Copilot, Gemini, humans |
| `check_run_annotations` | `check_run_annotations` | `GET /repos/{o}/{r}/commits/{sha}/check-runs` → `GET /check-runs/{id}/annotations` | any tool that annotates lines but posts no comment (incl. SonarCloud line issues) |
| `code_scanning` | `sarif_alerts` | `GET /repos/{o}/{r}/code-scanning/alerts?ref=refs/pull/{n}/head` | any SARIF uploader: Snyk, Semgrep, CodeQL, Trivy |

Tier 1 needs GitHub token scopes `pull_requests`, `checks: read`, `security_events: read`. **Check-run annotations are the highest-leverage single source** — most scanners emit file+line annotations even when silent in the comment thread.

### Tier 2 — vendor API (enabled iff the source's `token_env` is set)

| Source id | type | token_env | Endpoints |
|-----------|------|-----------|-----------|
| `sonarcloud` | `vendor_api` | `SONAR_TOKEN` | issues `GET https://sonarcloud.io/api/issues/search?componentKeys={key}&pullRequest={n}&resolved=false`; gate `GET https://sonarcloud.io/api/qualitygates/project_status?projectKey={key}&pullRequest={n}` — auth header `Authorization: Bearer $SONAR_TOKEN` (or basic `-u "$SONAR_TOKEN:"`) |
| `snyk` | `vendor_api` | `SNYK_TOKEN` | `GET https://api.snyk.io/rest/orgs/{org}/issues?...` (or consume via `code_scanning`) |
| `semgrep` | `vendor_api` | `SEMGREP_APP_TOKEN` | `GET https://semgrep.dev/api/v1/deployments/{slug}/findings?...` (or consume via `code_scanning`) |

## Registry row shape

```yaml
review_sources:
  - id: github_comments        type: github_comments        enabled_when: always
  - id: check_run_annotations  type: check_run_annotations  enabled_when: always
  - id: code_scanning          type: sarif_alerts           enabled_when: always
  - id: sonarcloud             type: vendor_api             token_env: SONAR_TOKEN
    precedence: gate_blocking_issues_review     # default (see below)
    project_discovery: [env:SONAR_PROJECT_KEY, file:sonar-project.properties, check_run:details_url]
  - id: snyk                   type: vendor_api  token_env: SNYK_TOKEN   enabled_when: token_present
  - id: semgrep                type: vendor_api  token_env: SEMGREP_APP_TOKEN  enabled_when: token_present
```

A source is **enabled** when `enabled_when: always` (Tier 1) or its `token_env` is present in the environment (Tier 2). A disabled or unreachable source is **skipped and recorded** in the run report (`summary.review_sources[].queried: false`) — it never blocks the run (fail-open per source).

**Active default (assumes `SONAR_TOKEN` is configured):** the shipped default in `SKILL.md` §Configuration enables the three GitHub-native sources **and `sonarcloud`** — SonarCloud is treated as a first-class source, not an opt-in example, with `SONAR_TOKEN` expected in the environment (repo Actions secret or web env). `snyk`/`semgrep` remain opt-in (uncomment + set their token). Because ingestion is fail-open, a missing `SONAR_TOKEN` at runtime degrades to "sonarcloud skipped, queried:false" rather than an error — so the default is safe in repos that don't use Sonar.

## Fetch → normalize contract

Every source normalizes into the **unified finding format** used by `signal-ingestion.md`:

```yaml
- id: "sonar-42"
  source: sonarcloud            # the registry id (also the "reviewer" for FP-rate)
  author: sonarcloud
  severity: blocking | actionable | discussion | deferred   # set by classifier per precedence
  file: "src/x.ts"
  line: 47
  message: "Remove this unused import."
  rule: "typescript:S1128"      # vendor rule id — used for cross-source dedup
  raw: "{original vendor payload}"
```

**Dedup across sources:** merge findings with the same `(file, line, rule)` (or `(file, line, message)` when no rule id). A vendor-API finding that also appears as a GitHub check annotation is one finding — keep the most precise message, keep the vendor `rule`.

## Precedence (default, per-source overridable)

- A scanner's **quality-gate pass/fail → `blocking`** (same tier as CI; must fix or explicitly defer to converge).
- A scanner's **individual line issues → review-tier** (`VALIDATE` in `finding-classifier.md`: validated against current code, confidence-gated, false-positive-rejectable).

Override per row via `precedence: gate_blocking | review_tier | gate_blocking_issues_review`.

## Setup & Secret Rotation

**Secrets never live in this pack.** The skill references token names only; you provide values via the environment.

**Set `SONAR_TOKEN`:**
- **CI (recommended):** repo *Settings → Secrets and variables → Actions → New repository secret* → `SONAR_TOKEN`. Pass it to the job as `env: SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}` (see the CI example in `SKILL.md`).
- **Agent runs:** add `SONAR_TOKEN` in your Claude-Code-on-the-web **environment settings**.
- Optional: `SONAR_ORG`, `SONAR_PROJECT_KEY` (else auto-discovered from `sonar-project.properties` or the SonarCloud check-run `details_url`).

**Smoke test (no skill required):**
```bash
curl -sS -u "$SONAR_TOKEN:" "https://sonarcloud.io/api/authentication/validate"      # -> {"valid":true}
curl -sS -u "$SONAR_TOKEN:" "https://sonarcloud.io/api/issues/search?componentKeys=$SONAR_PROJECT_KEY&pullRequest=<n>&resolved=false"
```

**Rotate a SonarCloud token:** *My Account → Security → Generate Tokens* (create new) → update the `SONAR_TOKEN` secret/env var → **Revoke** the old token. **Rotate immediately if a token is ever exposed** (committed, logged, or pasted into a chat). Never paste tokens into issues, PRs, or chat.

## Completeness check

- [ ] Every `enabled` source was queried (or recorded `queried: false` with a reason)
- [ ] Vendor findings normalized to the unified format with `rule` where available
- [ ] Cross-source duplicates merged by `(file, line, rule)`
- [ ] Each scanner gate status captured for `summary.review_sources`
- [ ] No token value appears in any finding, log, commit, or run report
