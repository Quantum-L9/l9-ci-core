# Reusable Node.js and TypeScript PR Pipeline

`node-pr-pipeline.yml` is the reusable CI workflow for npm-based Node.js and
TypeScript repositories.
It centralizes dependency installation, linting, type checking, builds, tests,
dependency auditing, secret scanning, dependency review, SBOM generation, and
the final required CI gate.

## Supported repositories

The initial contract supports repositories with:

- Node.js 18 or newer;
- npm;
- `package.json`;
- a committed `package-lock.json`;
- an npm test script unless `require-tests` is disabled.

Yarn, pnpm, Bun, and Deno are not part of the initial public contract.

## Basic caller

Create a thin caller workflow in the consumer repository:

```yaml
name: PR Pipeline
on:
  workflow_dispatch:
  pull_request:
    types:
      - opened
      - synchronize
      - ready_for_review
      - reopened
permissions:
  contents: read
jobs:
  node_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      node-version: "20"
```

Use a tested immutable commit SHA until a supported release reference is
published separately.

## TypeScript caller

```yaml
name: PR Pipeline
on:
  workflow_dispatch:
  pull_request:
    types:
      - opened
      - synchronize
      - ready_for_review
      - reopened
permissions:
  contents: read
jobs:
  node_pipeline:
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
```

This is the expected initial configuration for l9-meta-injector.

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

## Script behavior

Lint, type checking, and build scripts are optional.

When an optional script is configured but not present, the workflow emits a
notice and succeeds for that individual check.

Tests are required by default:

- a present test script is executed;
- a missing test script fails when `require-tests` is `true`;
- a missing test script emits a notice when `require-tests` is `false`.

Optional behavior is implemented inside each job. Required jobs are never
treated as successful merely because GitHub marked them as skipped.

## Security behavior

The security job runs:

- npm audit;
- Gitleaks with a checksum-verified release binary;
- GitHub dependency review for pull-request events.

By default, npm audit includes development dependencies because build,
transpilation, packaging, and test dependencies are part of the software supply
chain.

Set:

```yaml
audit-production-only: true
```

only when the repository deliberately wants runtime dependencies audited
separately.

Supported audit levels are:

- `low`;
- `moderate`;
- `high`;
- `critical`.

Invalid values fail validation before scanners run.

## Trusted command input

`install-command` is trusted workflow configuration.

Do not construct it from:

- pull-request titles or bodies;
- issue text;
- labels;
- commit messages;
- changed files;
- repository content controlled by an untrusted contributor.

The workflow passes the command through an environment variable and executes it
in a dedicated Bash process, but the caller remains responsible for providing a
trusted value.

> **Known limitation:** `cache: npm` and `cache-dependency-path` target
> `package-lock.json` unconditionally. Overriding `install-command` to a
> non-npm tool (e.g. `yarn install --frozen-lockfile`) will produce incorrect
> cache behaviour. Yarn, pnpm, Bun, and Deno are not part of the current
> contract.

## Monorepos

Set `working-directory` to the directory containing both `package.json` and
`package-lock.json`:

```yaml
jobs:
  node_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      working-directory: "packages/service-a"
```

All npm operations and the npm cache lockfile path use the configured working
directory.

## Final gate

The workflow exposes one aggregate check:

**Node CI Gate**

The gate requires successful results from:

- validation;
- lint and type checking;
- build and tests;
- security;
- SBOM.

An unsuccessful or unexpectedly skipped required job fails the final gate.

## Replacing a local workflow

Consumer repositories should remove copied CI implementation after validating
the reusable workflow against an immutable l9-ci-core commit.

The consumer workflow should retain only:

- event triggers;
- repository-specific input selection;
- the reusable workflow call.

Local checkout, Node setup, dependency installation, build, test, security, and
aggregation steps should be deleted rather than retained as a fallback.
