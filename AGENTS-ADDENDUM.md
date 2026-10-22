# AGENTS Addendum — Core versus SDK ownership

This addendum records the ownership boundary agents must respect when touching
the lint/analysis pipeline in `l9-ci-core`. It supplements `AGENTS.md`; on any
conflict, the executable gates (`make agent-check`, the tests under
`tests/workflows/`) are authoritative.

## The boundary

`l9-ci-core` is a **thin control plane**. Analysis *capability* — the scanners,
rulesets, formatters, and their execution — is owned by `l9-ci-sdk` and invoked
by Core through pinned, immutable references. Core never reimplements or copies
that capability.

The SDK has no Biome provider, so there is no SDK capability for Core to invoke
for the consumer-facing Biome check. Core therefore owns it end to end —
selecting, verifying and running a pinned upstream binary — and still parses
no provider report. That is tool selection and execution, which Core owns, not
SDK capability reimplemented.

| Concern | Owner | How Core uses it |
|---|---|---|
| Semgrep execution + rulesets + normalization | **SDK** | `invoke-sdk` `operation: semgrep-run` against a provisioned, pinned SDK. Core authors no `--config` list and parses no report. |
| Biome (JS/TS/JSON format + lint) — consumer-facing check | **Core** | Core owns version, install, execution and rollout: the version is the `biome` key of `install-consumer-ci/toolchain-lock.json`, `install-biome` installs the digest-locked binary, `run-biome-check` runs `biome ci --reporter=github` (advisory) and records only its exit code. Basis: Core owns "exact CI tool-version selection" (`.l9/architecture.yaml`); the SDK contract lists workflow permissions and exact tool selection under Core and prohibits the SDK selecting organization rollout (`l9-ci-sdk/.l9/integration-contract.yaml`); the SDK has no Biome provider. Running a pinned binary is not reimplementing SDK capability: Core parses no Biome report. ESLint is not a second formatter owner. |
| Biome — legacy presets/starters | **retired** | The copy-first preset and starter workflows that called `l9-ci-sdk/.github/workflows/l9-biome-scan.yml` were removed on 2026-10-22 (`AGENTS.md` §15). Only the locked contract `presets/typescript/biome.json` and `stamp.sh` remain; no Core surface references the SDK Biome workflow. |
| Toolchain alignment reporting | **Core** | `check-toolchain-alignment` reports consumer pin drift against the lock with `file:line`, advisory only. `install-consumer-ci/core-owned-tools.json` is the version-free tool list `Quantum-L9/.github` reads to render Dependabot ignore rules. |
| Ruff (Python format + lint) | **repo** | Ordinary consumer CI in the lint-test template; Core's own code is ruff-checked in `make agent-check`. |
| Type check (`tsc --noEmit`) and tests | **repo** | Kept in the consumer's `l9-lint-test` workflow, never removed by the Biome convergence. |
| SDK revision allowlist + reusable-workflow declarations | **Core** | `.l9/sdk-compatibility.yaml` — the pinned SDK revisions and the reusable workflows they export. |

## Rules for agents

- **Do not copy SDK implementation into Core.** Reference it by pinned SHA.
- **Pin every SDK reference to a full 40-character commit SHA.** No floating
  refs, branches, or tags (enforced by `.l9/sdk-compatibility.yaml` policy).
- **One formatter per language.** Biome owns JS/TS/JSON; ruff owns Python. Do
  not add ESLint/Prettier as a competing format authority.
- **Never remove `tsc` type checking or the repository test suite** when
  converging linting onto Biome.
- **Least privilege.** Preset and starter workflows stay `contents: read`; the
  Biome reusable workflow is read-only, and so are Core's Biome actions.
- **Advisory-to-blocking rollout** for the legacy presets is controlled by the
  `enforce-biome` input, not by removing the gate. Core's own Biome check is
  advisory in this revision; promotion to blocking is a later governed change
  through `resolve-governance` defaults, never a consumer input.
- **One version authority.** Every tool version Core installs or selects is a
  key of `install-consumer-ci/toolchain-lock.json`; every other copy in Core is
  bound to it by a test. Bump the lock and its bound copies in one human
  pin-file PR; no Dependabot entry maintains the lock.
- **Never hand-author `biome.json`.** Stamp the locked TypeScript contract with
  `presets/typescript/stamp.sh` (`l9-ci-activation-typescript` Step 2b). Extra
  path excludes may be appended to `files.includes`; do not rewrite formatter
  or linter blocks.
