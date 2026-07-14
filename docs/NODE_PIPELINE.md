# Reusable Node.js and TypeScript PR Pipeline

`node-pr-pipeline.yml` is the reusable CI workflow for npm-based Node.js and
TypeScript repositories.
It centralises dependency installation, linting, type checking, builds, tests,
dependency auditing, secret scanning, dependency review, SBOM generation,
semgrep policy checks, OpenSSF Scorecard, and the final required CI gate.
The l9-ci SDK drives governance threshold resolution and agent review payload
emission at every stage — identical to the Python pipeline contract.

## Supported repositories

- Node.js 18 or newer
- npm with a committed `package-lock.json`
- An npm test script unless `require-tests` is disabled

Yarn, pnpm, Bun, and Deno are not part of the initial public contract.

## Basic caller

Create a thin caller workflow in the consumer repository.
The caller is responsible for computing `changed-files` and `pr-labels` in a
`context` job before delegating to this workflow.

```yaml
name: PR Pipeline
on:
  workflow_dispatch:
  pull_request:
    types: [opened, synchronize, ready_for_review, reopened]
permissions:
  contents: read
jobs:
  context:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
      pull-requests: read
    outputs:
      changed-files: ${{ steps.changed.outputs.files }}
      pr-labels: ${{ steps.labels.outputs.labels }}
      labels-known: ${{ steps.labels.outputs.known }}
    steps:
      - uses: actions/checkout@<sha> # vX.Y.Z
        with:
          fetch-depth: 0  # Required for git diff against base branch
      - name: Resolve Changed Files
        id: changed
        run: |
          FILES=$(git diff --name-only origin/${{ github.base_ref }}...HEAD | paste -sd,)
          echo "files=$FILES" >> "$GITHUB_OUTPUT"
      - name: Resolve PR Labels
        id: labels
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            LABELS=$(echo '${{ toJson(github.event.pull_request.labels.*.name) }}' \
              | python3 -c "import sys,json; print(','.join(json.load(sys.stdin)))")
            echo "labels=$LABELS" >> "$GITHUB_OUTPUT"
            echo "known=true" >> "$GITHUB_OUTPUT"
          else
            echo "labels=" >> "$GITHUB_OUTPUT"
            echo "known=false" >> "$GITHUB_OUTPUT"
          fi

  node_pipeline:
    needs: context
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      node-version: "20"
      changed-files: ${{ needs.context.outputs.changed-files }}
      pr-labels: ${{ needs.context.outputs.pr-labels }}
      labels-known: ${{ needs.context.outputs.labels-known == 'true' }}
    secrets:
      SDK_TOKEN: ${{ secrets.SDK_TOKEN }}
```

Use a tested immutable commit SHA until a supported release reference is
published separately.

## TypeScript caller

```yaml
  node_pipeline:
    needs: context
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      node-version: "20"
      working-directory: "."
      lint-script: ""
      typecheck-script: "typecheck"
      build-script: "build"
      test-script: "test"
      require-tests: true
      audit-level: "high"
      changed-files: ${{ needs.context.outputs.changed-files }}
      pr-labels: ${{ needs.context.outputs.pr-labels }}
      labels-known: ${{ needs.context.outputs.labels-known == 'true' }}
    secrets:
      SDK_TOKEN: ${{ secrets.SDK_TOKEN }}
```

## Inputs

| Input | Type | Default | Purpose |
| --- | --- | --- | --- |
| `node-version` | string | `"20"` | Node.js version |
| `working-directory` | string | `"."` | Directory containing the npm package |
| `install-command` | string | `"npm ci"` | Trusted dependency installation command |
| `lint-script` | string | `"lint"` | npm lint script; empty disables |
| `typecheck-script` | string | `"typecheck"` | npm type-check script; empty disables |
| `build-script` | string | `"build"` | npm build script; empty disables |
| `test-script` | string | `"test"` | npm test script |
| `require-tests` | boolean | `true` | Fail when the test script is absent |
| `audit-level` | string | `"high"` | npm audit failure threshold |
| `audit-production-only` | boolean | `false` | Exclude development dependencies from audit |
| `upload-sbom` | boolean | `true` | Generate and upload an SPDX JSON SBOM |
| `changed-files` | string | `""` | Comma-separated changed-file list (from context job) |
| `pr-labels` | string | `""` | Comma-separated PR label list (from context job) |
| `labels-known` | boolean | `true` | `false` when event carries no label data (push) |
| `l9-ci-install-command` | string | SDK default | Override to pin a specific SDK commit SHA |

## Secrets

| Secret | Required | Purpose |
| --- | --- | --- |
| `SDK_TOKEN` | No | GitHub token for private l9-ci SDK access; omit in forks/open-source repos |

## Pipeline stages

| Job | Stage | Gate weight |
| --- | --- | --- |
| `validate` | validate | blocking |
| `lint` | lint | blocking |
| `semgrep` | — | blocking (skips when `.semgrep/` absent) |
| `test` | test | blocking |
| `security` | security | blocking |
| `sbom` | — | required |
| `scorecard` | — | advisory (`security-events: write`, `id-token: write`, `actions: read` required) |
| `ci-gate` | gate | final |

## Security behavior

The security job runs **blocking** scanners and one **advisory** tier:

**Blocking:** npm audit (at `audit-level`), Gitleaks (`--redact`), dependency review (PR only).

**Advisory (never blocks):** `npm audit --audit-level=none` — surfaced as a `::notice` for visibility without failing the job.

## Governance and SDK

Every job installs the l9-ci SDK and runs `l9-ci run-pipeline --stage <stage>`. The SDK:

- resolves governance thresholds from `.github/governance/quality-thresholds.yaml`
- validates rule modes from `.github/governance/rule-modes.yaml`
- emits structured `artifacts/ci/*` summaries downloaded by `ci-gate`
- writes `artifacts/agent_review_payload.json` for automated review workflows

Override the SDK version with:

```yaml
with:
  l9-ci-install-command: 'pip install "l9-ci @ git+https://github.com/Quantum-L9/l9-ci-sdk.git@<sha>"'
```

## Context job and label routing

`ci-gate` reads `changed-files`, `pr-labels`, and `labels-known` to apply
risk-tier routing identical to the Python pipeline.
When `labels-known` is `false` (push events), the gate defaults to the
highest risk tier rather than an open gate.

## Trusted command input

`install-command` and `l9-ci-install-command` are trusted workflow configuration.
Do not construct them from pull-request titles, issue text, labels, commit
messages, changed files, or any content controlled by an untrusted contributor.

> **Known limitation:** `cache: npm` and `cache-dependency-path` target
> `package-lock.json` unconditionally. Overriding `install-command` to a
> non-npm tool produces incorrect cache behaviour.

## Monorepos

```yaml
with:
  working-directory: "packages/service-a"
```

## Final gate

**Node CI Gate** requires successful (or skipped) results from:
validate · lint · semgrep · test · security · sbom · scorecard.

Result evaluation is delegated to `l9-ci gate` — `skipped` is treated as
passing for event-conditional steps such as `dependency-review` on push events.

## Replacing a local workflow

Consumer repositories should remove copied CI implementation after validating
the reusable workflow against an immutable l9-ci-core commit.

Retain only: event triggers, repository-specific input selection, the `context`
job, and the reusable workflow call.
