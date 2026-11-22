# CodeQL is one org-wide check, judging only what a change introduces (2026-11-21)

A decision record, kept outside `docs/adr/` like
[`security-checks.md`](security-checks.md).

## Status

Accepted 2026-11-21, operator decision: one org-wide `CodeQL` check replaces
per-repository CodeQL workflows and CodeQL default setup. Detected languages,
always including `actions`; the `security-extended` suite; build-mode none;
pull requests are judged on new alerts only; a nightly full run; blocking
proposed at error / high and above.

**Decision owner:** `Quantum-L9/l9-ci-core`. **Related:**
[`blocking-check-classification.md`](blocking-check-classification.md),
[`nightly-findings-sink.md`](nightly-findings-sink.md), AGENTS.md §40, §48,
§49, L9-CI-CORE-005.

## Decision

| Where | What CodeQL judges | Outcome |
|---|---|---|
| `org-ci.yml` job `codeql` (`CodeQL`), pull request / merge group / default-branch push | the evaluated revision **and** the event's base, analyzed with the same CodeQL; only results the base does not have | a new result at level `error`, or with security severity 7.0 (high) or above, fails the check, annotated at file and line; any other new result is a warning |
| … any other event (dispatch, the promotion canary) | the whole tree, no base | nothing is new: the summary reports the count and the check passes |
| `nightly-sink.yml` | the whole default branch | SARIF to code scanning, one category per language (`l9-central/codeql-<language>/`), counted by the nightly ratchet |

### Mechanism (verified)

- **No upload on a pull request.** `org-ci.yml` is a workflow an untrusted
  pull request triggers, so every job holds `contents: read` only
  (L9-CI-CORE-005). Uploading CodeQL results needs `security-events: write`
  (github/codeql-action README: "All advanced setup code scanning workflows
  must have the `security-events: write` permission"), so the check never
  uploads and does not use `codeql-action`. It runs the CodeQL CLI from the
  official bundle (`codeql-bundle-v2.27.5`, `codeql-bundle-linux64.tar.zst`,
  sha256 `141e6eb4…92edf0`), checksum-verified like gitleaks.
- **Only new results.** Both revisions are analyzed with build-mode none and
  `codeql/<language>-queries:codeql-suites/<language>-security-extended.qls`.
  A head result is new when the base has no result with the same rule, path
  and `partialFingerprints.primaryLocationLineHash` (the CLI's own line
  fingerprint, the key code scanning matches alerts on), counted with
  multiplicity. A pull request's revision is GitHub's merge commit, so the
  difference is exactly what the pull request brings in.
- **Languages.** By the files git tracks: `actions` for any workflow or
  action file (every governed repository has one), `python`, `javascript`
  (JavaScript and TypeScript), `ruby`, `java`, `csharp`, `rust`: the
  languages CodeQL analyzes without a build. CodeQL refuses a database for a
  language with no source on one side (exit 32, "no source code seen"); that
  side then has no results for it. Go, Swift and Kotlin need a build and are
  not analyzed; C/C++ `none` is a preview and is not used.
- **Severity.** GitHub's own pull-request check fails on "error, critical, or
  high" (docs: triaging code scanning alerts in pull requests); the same
  threshold is used here: `level: error` or `security-severity >= 7.0`.
  `L9_CODEQL_MODE: advisory` on the verdict step turns every finding into a
  warning, a one-line change if blocking is not accepted.
- **Default setup.** "Default setup overrides existing CodeQL setups by
  disabling any existing CodeQL workflows, and blocking any CodeQL analysis
  API uploads" (docs: two CodeQL workflows). The pull-request check uploads
  nothing and is not affected. The nightly upload is refused while a
  repository's default setup is enabled; the sink then publishes every other
  category first and fails the night naming the refused CodeQL categories.

## Migration (operator)

Order (operator): **(a)** this check lands on Core `main` and reports once;
**(b)** the per-repository CodeQL files are deleted; **(c)** CodeQL default
setup is turned off where it is enabled. Default setup blocks the nightly
CodeQL upload, so a repository in the nightly sink with default setup still
on has a red night, naming the refused CodeQL categories, from (a) until (c);
its other categories still publish. Doing (c) the same day as (a), or before
(b), avoids that.

**(b) Files to delete** (one pull request per repository; each repository's
own owners merge):

| Repository | Files |
|---|---|
| `Cognitive.Engine.Graphs` | `.github/workflows/codeql.yml` |
| `Enrichment.Inference.Engine` | `.github/workflows/codeql.yml` |
| `L9_Original_Repo` | `.github/workflows/codeql.yml` |
| `l9-graphiti-memory` | `.github/workflows/codeql.yml` |
| `Cursor-Governance` | `.github/workflows/codeql.yml`, `.github/workflows/codeql-nightly.yml`, `.github/workflows/codeql-reusable.yml`, `.github/codeql/codeql-config.yml`, `.github/codeql/codeql-config-nightly.yml` |

**(c) Default setup off.** Confirmed enabled: `Quantum-L9/l9-ci-core` and
`Quantum-L9/.github` (actions, python and, on `.github`, javascript /
typescript; extended suite). Confirmed not enabled: `Quantum-L9/Cursor-Governance`.
Every other repository was not readable from Core's session (HTTP 403) and
must be checked. Per repository: GitHub → the repository → Settings → Code
security (Advanced Security) → Code scanning → CodeQL analysis → "⋯" →
Disable CodeQL. Organization-wide: Settings → Code security → Configurations →
the configuration applied to the repositories → CodeQL analysis → Not set (or
Disabled) → Save configuration → apply to the repositories.

Not CodeQL analysis, keep: the `codeql-action/upload-sarif` steps that upload
Trivy, Scorecard or Core's own SARIF (`Cognitive.Engine.Graphs`,
`Enrichment.Inference.Engine`, `L9_Original_Repo`, `l9-goose`, Core
`publish-sarif.yml`). The inventory searched every public repository's
`.github` tree and the code-search index for private ones; a private
repository the index does not cover is unconfirmed.

Required-check names the deleted workflows report ("Analyze (python)",
"Detect CodeQL languages", "Analyze Python Code", "CodeQL Analysis",
"analyze"): remove any that a ruleset or branch protection requires, in the
same change.

**Requiredness.** GitHub → Quantum-L9 → Settings → Rules → Rulesets →
`l9-canonical-ci-required` (21895545) → "Require status checks to pass" → Add
checks → `CodeQL` → Save changes, once the check has reported on a governed
repository.

## Consequences

- A pull request runs CodeQL twice (base and head). Measured on Core with the
  pinned bundle: python 18 s create + 19 s analyze, actions 5 s + 13 s per
  side.
- The promotion canary runs the check as a sibling of every leg (no base:
  whole tree, no verdict failure), so a Core change that breaks the job on a
  canary repository is a regression.
- Code scanning keeps no pull-request alerts from this check; the record of
  existing alerts is the nightly category.
