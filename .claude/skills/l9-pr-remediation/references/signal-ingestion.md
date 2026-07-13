<!-- L9_META
l9_schema: 1
parent: l9-pr-remediation
layer: reference
role: signal_ingestion
tags: [pr, ci, review, ingestion, scope-discovery, github-api, gate-discovery]
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-07-13
/L9_META -->

# Signal Ingestion

## Purpose

Resolve the PR scope, then fetch all actionable signals from each in-scope PR: CI gate failures, code-review comments, inline suggestions, and workflow definitions. Normalize everything into a unified finding list for classification.

## Scope Discovery (FIRST — before any per-PR work)

Expand the configured `pr_scope` into a concrete list of PR numbers. Run the per-PR loop (gate discovery → ingest → … → report) independently for each. **Never share a commit across PRs.**

```bash
# ALL_OPEN
gh pr list --repo {owner}/{repo} --state open --json number,headRefName,author,labels

# PR_NUMBERS: use the given list directly
# LABEL:<x>
gh pr list --repo {owner}/{repo} --state open --label "{x}" --json number,headRefName

# AUTHOR:<u>
gh pr list --repo {owner}/{repo} --state open --author "{u}" --json number,headRefName
```

Produce the scope artifact:

```yaml
pr_scope_resolved:
  repo: "{owner}/{repo}"
  selector: "ALL_OPEN | PR_NUMBERS:[...] | LABEL:x | AUTHOR:u"
  prs:
    - number: 42
      branch: "feat/x"
    - number: 43
      branch: "fix/y"
  total: 2
```

If scope resolves to zero PRs → STOP and report "no PRs in scope".

## Gate Discovery (per PR — before CI log ingestion)

### Step 0: Parse workflow YAML

```bash
find .github/workflows -name "*.yml" -o -name "*.yaml"
cat .github/workflows/*.yml
```

For each workflow file extract: job names + `runs-on`, step names + `run:` commands, `if:` conditions, and required `env:` blocks. Build the **gate registry**:

```yaml
ci_gates:
  - gate: "type-check"
    command: "npx tsc --noEmit"
    workflow: "build-and-validate.yml"
    job: "validate"
    step: "Type check"
    can_run_locally: true
    requires_secrets: false
```

Also check `package.json` scripts and any `Makefile` `ci`/`pr-check` targets:

```bash
cat package.json | grep -A20 '"scripts"'
```

This gate registry drives fix-engine local verification. (Adapter: if no workflows exist, derive gates from `package.json`/`Makefile`; if none, ask the user for the verify command before fixing.)

## CI Signal Ingestion

```bash
# Latest run on the PR branch
gh run list --branch {branch} --limit 3 --json databaseId,status,conclusion,event
# Failed logs (pick most recent run with conclusion != success)
gh run view {RUN_ID} --log-failed
# Job-level summary if logs are large
gh run view {RUN_ID} --json jobs --jq '.jobs[] | select(.conclusion=="failure") | {name, conclusion}'
```

Parse each failure into: gate name (match the registry), error message, file + line when available, and the exact command CI ran.

## Review Comment Ingestion

```bash
# Reviews (state CHANGES_REQUESTED / COMMENTED)
gh api /repos/{owner}/{repo}/pulls/{pr}/reviews --jq '.[] | {id, user: .user.login, state, body}'
# Inline (diff) comments — include suggestion blocks
gh api /repos/{owner}/{repo}/pulls/{pr}/comments --jq '.[] | {id, user: .user.login, path, line, body, created_at}'
# General PR comments
gh pr view {pr} --repo {owner}/{repo} --comments --json comments
```

### Thread resolution state (GraphQL) — only process `isResolved: false`

```bash
gh api graphql -f query='
  query($owner:String!,$repo:String!,$pr:Int!){
    repository(owner:$owner,name:$repo){
      pullRequest(number:$pr){
        reviewThreads(first:100){ nodes{
          id isResolved
          comments(first:5){ nodes{ body author{login} path line } } } } } } }
' -f owner={owner} -f repo={repo} -F pr={pr}
```

Capture each thread's `id` (needed later to resolve it). Skip threads already resolved or clearly addressed by a later commit (idempotency).

## Unified Finding Format

```yaml
findings:
  - id: "ci-1"
    source: ci | review_inline | review_general
    author: "github-actions" | "gemini-code-assist" | "coderabbitai" | "sonarcloud" | "{human}"
    severity: blocking | actionable | discussion | deferred
    file: "src/index.ts"        # null for general comments
    line: 42                    # null for general comments
    thread_id: "PRRT_..."       # for review threads (to resolve later)
    has_suggestion_block: true | false
    message: "Type error: Property 'foo' does not exist on type 'Bar'"
    gate: "type-check"          # null for review comments
    local_verify_command: "npx tsc --noEmit"
    raw: "full original text"
```

## Bot Detection

`gemini-code-assist[bot]` → Gemini · `coderabbitai[bot]` → CodeRabbit · `copilot[bot]` → GitHub Copilot · `sonarcloud[bot]` → SonarCloud · `github-actions[bot]` → CI · all others → human.

## Deduplication

When a review comment references the same file+line as a CI error, merge into one finding: keep the CI error message (more precise), retain the review comment's suggested fix if present.

## Ingestion Completeness Check

- [ ] Scope resolved to a concrete PR list
- [ ] All workflow files read; gate registry built (≥1 gate)
- [ ] All CI failures mapped to a registry gate
- [ ] All unresolved review threads captured with `thread_id`
- [ ] All inline suggestions captured with file+line + `has_suggestion_block`
- [ ] Bot vs human attribution correct
- [ ] Duplicates merged
