# Locked Biome contract (TypeScript / JavaScript consumers)

This directory ships the one consumer-facing Biome artifact Core owns:

| File | Purpose |
|---|---|
| `biome.json` | Locked Biome 2.5.8 contract (format + lint + assist). **Stamp it; do not hand-author `biome.json`.** |
| `stamp.sh` | Copies the contract, `.biomeignore`, `.editorconfig`, and `.vscode/extensions.json` (the `biomejs.biome` editor recommendation) into a consumer. Existing files are kept. |

The Biome version is the `biome` key of
`.github/actions/install-consumer-ci/toolchain-lock.json`; the `$schema`
here is test-bound to it.

## How CI runs Biome

No workflow is copied. The organization workflow (`org-ci.yml`, required by
the organization ruleset) runs Core's own `install-biome` (digest-locked
binary) and `run-biome-check` (`biome ci --reporter=github`) against every
governed repository whose root has `biome.json` or `biome.jsonc`. A consumer
without a Biome config is `not_applicable`; nothing is downloaded.

## Activate

```bash
git clone --depth=1 https://github.com/Quantum-L9/l9-ci-core.git /tmp/l9-ci-core
bash /tmp/l9-ci-core/presets/typescript/stamp.sh "$(pwd)"
```

Extra path excludes may be appended to `files.includes` after the stamp. Do
not rewrite the `formatter` / `linter` / `javascript` / `json` blocks, and do
not add ESLint or Prettier as a second JS/TS/JSON formatter owner. `tsc
--noEmit` and the repository test suite stay in the repository's own
`Repo.mk` leaves (see `docs/ACTIVATION.md`).

## History

The copy-first preset workflows (`l9-analysis.yml`, `l9-lint-test.yml`) and
the preset governance pack that used to live beside this file were retired on
2026-10-22 (`AGENTS.md` §15). They are not distributed from
`Quantum-L9/.github` either.
