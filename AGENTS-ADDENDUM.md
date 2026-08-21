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

| Concern | Owner | How Core uses it |
|---|---|---|
| Semgrep execution + rulesets + normalization | **SDK** | `invoke-sdk` `operation: semgrep-run` against a provisioned, pinned SDK. Core authors no `--config` list and parses no report. |
| Biome (JS/TS/JSON format + lint) | **SDK** | Presets call the reusable workflow `l9-ci-sdk/.github/workflows/l9-biome-scan.yml@<full-sha>`. ESLint is not a second formatter owner. |
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
  Biome reusable workflow is read-only.
- **Advisory-to-blocking rollout** is controlled by the `enforce-biome` input,
  not by removing the gate. Adopt advisory, drive to clean, then flip to
  blocking. See `presets/typescript/README.md`.
- **Never hand-author `biome.json`.** Stamp the locked TypeScript contract with
  `presets/typescript/stamp.sh` (`l9-ci-activation-typescript` Step 2b). Extra
  path excludes may be appended to `files.includes`; do not rewrite formatter
  or linter blocks.
