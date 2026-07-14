# Node.js / TypeScript PR Pipeline

`.github/workflows/node-pr-pipeline.yml` is a reusable GitHub Actions workflow
that provides a standardized L9 pull-request pipeline for **npm-based** Node.js
and TypeScript repositories.

It is **additive and independent** of the Python `pr-pipeline.yml`. It installs
no Python, runs no `pip`, and does not depend on the `l9-ci` SDK. Adding it to a
repository does not change any Python consumer.

## Supported project contract

The first version supports repositories with:

- Node.js 18 or newer (the pipeline defaults to Node.js 20);
- **npm** as the package manager;
- a committed `package.json`;
- a committed `package-lock.json`;
- an npm `test` script;
- optional `build`, `lint`, and `typecheck` scripts.

npm is the only supported package manager in v1. There is no `package-manager`
input: Yarn, pnpm, Bun, and Deno are out of scope until fully implemented and
tested.

## Minimal caller

```yaml
name: Node PR Pipeline
on:
  pull_request:
permissions:
  contents: read
jobs:
  node_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      node-version: "20"
```

## TypeScript caller

```yaml
name: TypeScript PR Pipeline
on:
  pull_request:
permissions:
  contents: read
jobs:
  node_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      node-version: "20"
      lint-script: ""          # empty disables the lint command
      typecheck-script: "typecheck"
      build-script: "build"
      test-script: "test"
      audit-level: "high"
```

> **Pinning.** Release tags are outside the scope of this work. Until a separate
> release process publishes a supported tag, callers should reference a tested
> **immutable commit SHA** (`@<commit-sha>`) rather than `@main`, a branch name,
> or `@v1`.

## Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `node-version` | string | `"20"` | Node.js version used by the pipeline. |
| `working-directory` | string | `"."` | Directory containing `package.json` and `package-lock.json`. |
| `install-command` | string | `"npm ci"` | Trusted dependency-installation command (see security note). |
| `lint-script` | string | `"lint"` | npm script used for linting. Empty disables the lint command. |
| `typecheck-script` | string | `"typecheck"` | npm script used for type checking. Empty disables it. |
| `build-script` | string | `"build"` | npm script used for the build. Empty disables it. |
| `test-script` | string | `"test"` | npm script used for tests. |
| `require-tests` | boolean | `true` | Fail when the configured test script is absent. |
| `audit-level` | string | `"high"` | Minimum `npm audit` severity that fails the security job (`low`, `moderate`, `high`, `critical`). |
| `audit-production-only` | boolean | `false` | Audit runtime dependencies only (`npm audit --omit=dev`). |
| `run-semgrep` | boolean | `true` | Run Semgrep when a `.semgrep` directory exists. |
| `upload-sbom` | boolean | `true` | Generate and upload an SBOM artifact. |

No secrets are required. Private npm-registry support is intentionally not part
of v1 because it introduces token handling and fork-pull-request considerations
that must be designed separately.

## Default behavior and optional scripts

- An **empty** optional script input (`lint-script: ""`, `typecheck-script: ""`,
  `build-script: ""`) disables that check. The owning job still completes
  **successfully** through an intentional internal no-op — it is not reported as
  a GitHub-level skipped job.
- A **configured but missing** optional script emits a `::notice::` and is
  skipped, without failing the job.
- Tests remain **required** unless you explicitly set `require-tests: false`.
  With `require-tests: true` (the default), a missing test script fails the
  `test` job (and therefore the gate).
- Coverage policy is owned entirely by the consumer's `test` script. The
  pipeline does not infer or enforce a coverage tool in v1.

## Security behavior

- **`npm audit`** runs at the configured `audit-level`. By default it audits all
  dependencies, because TypeScript build and release outputs may depend on dev
  dependencies. Set `audit-production-only: true` to audit runtime dependencies
  only.
- **Gitleaks** runs from the repository root over the full checked-out tree. The
  release archive is downloaded and its SHA-256 is verified against the digest
  recorded in `.github/governance/download-integrity.yaml` **before** the binary
  is unpacked or executed. No unverified binary is ever run.
- **Dependency review** runs only on `pull_request` events (it needs a base to
  compare against). On other event contexts the step is skipped, not failed.
- **Semgrep**, when `run-semgrep: true` and a `.semgrep/` directory exists, runs
  from the official `semgrep/semgrep` image pinned by an immutable digest, so no
  Python toolchain enters the Node pipeline.

## Trusted command inputs

`install-command` is a **trusted workflow configuration** value. It is passed
through an environment variable and executed in a dedicated strict shell; it is
never spliced directly into a larger shell program. All other lifecycle
operations run npm **script names** (`npm run "$SCRIPT"`) rather than arbitrary
commands, with the script name passed via the environment.

Command inputs must **never** be derived from pull-request content, issue text,
labels, commit messages, or any other untrusted value.

## Monorepo / non-root working directory

Point `working-directory` at the package that owns the `package.json` and
`package-lock.json`. npm caching, installation, script execution, and
`npm audit` all run in that directory.

```yaml
jobs:
  node_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      working-directory: "packages/service-a"
      typecheck-script: "typecheck"
      build-script: "build"
```

Repository-wide scanners such as Gitleaks always run from the repository root.

## Jobs and the CI gate

```
validate ─┬─> lint ──────┐
          ├─> test ──────┤
          ├─> security ──┤──> ci-gate
          ├─> semgrep ───┤
          └─> sbom ──────┘
```

`validate` fails fast on an invalid consumer contract (missing directory,
missing or invalid `package.json` / `package-lock.json`, an invalid
`audit-level`, or a missing required test script). `lint`, `test`, `security`,
`semgrep`, and `sbom` then run in parallel. The final `ci-gate` runs with
`if: always()` and requires **every** dependency to have result `success` — an
unexpected `skipped` result is treated as a failure, not a pass. Make `ci-gate`
the required status check in branch protection.
