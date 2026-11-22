# Core gitleaks rules for every repository (2026-11-21)

A decision record, kept outside `docs/adr/` like
[`security-checks.md`](security-checks.md).

## Status

Accepted 2026-11-21, operator-directed correction (campaign invariant: every
tool's configuration comes from Core; consumers configure nothing by hand).

**Decision owner:** `Quantum-L9/l9-ci-core`. **Related:**
[`security-checks.md`](security-checks.md) (the `gitleaks` check), AGENTS.md
§49.

## The defect

gitleaks keeps one rule per id. The gitleaks step (org-ci.yml's `gitleaks`
check and security.yml, the v1 kernel) appended `[extend] useDefault = true` to
a repository's `.gitleaks.toml` without looking at its rule ids, so a
repository rule whose id is also a gitleaks default rule was silently replaced
by the default. `Enrichment.Inference.Engine` lost four rules this way
(`openai-api-key`, `generic-api-key`, `slack-bot-token`, `private-key`); its
`sk-[a-zA-Z0-9]{20,}` rule caught OpenAI keys the default (which requires the
`T3BlbkFJ` marker) misses. `Cognitive.Engine.Graphs` loses the same four ids.

## Decision

- **The scan config is the Core preset plus an allowlist.** The composer
  writes `[extend] useDefault = true`, the Core `[[rules]]` block, and the
  repository's `[allowlist]` when `.gitleaks.toml` has one. Repository
  `[[rules]]` and `[extend].path` are not copied and do not fail the job.
  gitleaks' defaults stay checksum-pinned and apply through `useDefault`.
  `.gitleaksignore` stays a file gitleaks reads from the tree.
- **Core rules for every repository.** Rules that caught what the defaults
  miss move into Core's preset (`presets/security/.gitleaks.toml`, between its
  "Core rules" markers; every id `l9-`). The gitleaks step embeds the same
  block (a test binds the two) and applies it in every repository, including
  one whose `.gitleaks.toml` still names `openai-api-key`, `generic-api-key`,
  `slack-bot-token`, or `private-key`.
- **Tests.** `tests/workflows/test_gitleaks_core_rules.py`: those four ids
  still produce a scan config, an allowlist is kept, the binding, and per
  Core rule a sample it catches; with a real gitleaks, that the 8.24.3
  defaults alone find nothing in that sample.

## Inventory (default branches, 2026-11-21)

Every repository whose `.gitleaks.toml` could be read (public repositories by
clone; the others are not readable from Core's session):

| Repository | `.gitleaks.toml` | Custom rules |
|---|---|---|
| `Enrichment.Inference.Engine` | own, no `[extend]` | 10 (4 collide) |
| `Cognitive.Engine.Graphs` | own, no `[extend]`, allowlists | 12 (4 collide) |
| `Cursor-Governance` | own, allowlists | 0 |
| `l9-node-template`, `l9-observability-core` | `[extend] useDefault` | 0 |
| `l9-ci-core` | the preset | (Core rules) |
| `l9-ci-sdk`, `Gate_SDK`, `.github` | none | none |

Each custom rule, against gitleaks 8.24.3's defaults:

| Repository | Rule id (line) | Disposition |
|---|---|---|
| EIE | `openai-api-key` (19) | **collides; transplanted** as `l9-openai-api-key` (no marker required) |
| EIE | `perplexity-api-key` | **transplanted** as `l9-perplexity-api-key` (no default) |
| EIE | `generic-api-key` (31) | **collides; transplanted** as `l9-api-key-assignment` (any entropy; quoted literal) |
| EIE | `aws-access-key` | superseded by `aws-access-token` |
| EIE | `slack-bot-token` (52) | **collides; transplanted** as `l9-slack-bot-token` (digit groups of any length) |
| EIE | `postgres-connection-string` | **transplanted** as `l9-postgres-url-password` |
| EIE | `redis-connection-string` | **transplanted** as `l9-redis-url-password` |
| EIE | `private-key` (81) | **collides; transplanted** as `l9-private-key-header` (header alone) |
| EIE | `jwt-token` | **transplanted** as `l9-jwt` (shorter segments than `jwt`) |
| EIE | `sendgrid-api-key` | superseded by `sendgrid-api-token` |
| CEG | `openai-api-key` | collides; superseded (equals the default's second form) |
| CEG | `openai-project-key` | **transplanted** as `l9-openai-project-key` (no marker required) |
| CEG | `generic-api-key` | collides; transplanted (merged into `l9-api-key-assignment`) |
| CEG | `aws-api-key` | superseded by `aws-access-token` |
| CEG | `slack-bot-token` | collides; superseded by the default |
| CEG | `sendgrid-api-key` | superseded by `sendgrid-api-token` |
| CEG | `neo4j-connection` | **transplanted** as `l9-neo4j-url-password` |
| CEG | `neo4j-password-assignment` | **transplanted** as `l9-neo4j-password-assignment` |
| CEG | `redis-connection` | transplanted (merged into `l9-redis-url-password`) |
| CEG | `postgres-connection` | transplanted (merged into `l9-postgres-url-password`) |
| CEG | `private-key` | collides; transplanted (merged into `l9-private-key-header`) |
| CEG | `jwt-token` | transplanted (merged into `l9-jwt`) |

`l9-api-key-assignment` takes EIE's quoted form: CEG's unquoted form matches
identifiers and environment lookups (`api_key = settings_value_from_env`).

### What the Core rules find today (current trees, with each repository's own allowlists)

`Cognitive.Engine.Graphs` 3, `Cursor-Governance` 5, `Enrichment.Inference.Engine`
3, `l9-ci-sdk` 2, `Gate_SDK` 0, `l9-ci-core` 0: placeholder credentials in
compose files, docs and test fixtures. A pull request is judged on its own
commits, so these surface only nightly (the night they first appear, the
ratchet fails once); a reviewed one is recorded in the repository's
`.gitleaksignore` by fingerprint.

## Consequence for two repositories

`Enrichment.Inference.Engine` and `Cognitive.Engine.Graphs` keep their
`.gitleaks.toml` files. Those `[[rules]]` are not copied, so the four default
ids no longer replace a default and no longer fail the check. The broader
detections live in the Core rules above. Line numbers in the inventory are
EIE's. No consumer edit is required for this to go green.
