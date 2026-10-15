# Consumer lint + test workflow

Generic ruff / mypy / pytest versions are **Core-owned**. Callers install them
with the composite action; they do not copy pin files and Dependabot does not
bump those packages.

```yaml
- uses: Quantum-L9/l9-ci-core/.github/actions/install-consumer-ci@v2
```

`@v2` is a floating compatibility tag moved only after a human Core pin-file
PR, by `tools/publish_consumer_ci_tag.sh`. It is **not** a Core release alias
(Core releases never move it, see `.l9/release-plane.yaml`) and it is **not**
a governed organization-enforcement path (that is Core `main` via the
organization ruleset). Its normal consumer use is the installer; the sole
additional use is the named Cognitive Runtime release integration recorded in
[`docs/release/consumer-integration-channel.md`](release/consumer-integration-channel.md).
Never `@main`.

Authority files in Core:

- `.github/actions/install-consumer-ci/toolchain-lock.json` — **the** organization
  toolchain-version authority: `ruff`, `mypy`, `pytest`, `biome`, `semgrep`,
  `python`, `pre-commit`, `pip`. Every other Core copy of a version is bound to
  it by a test.
- `.github/actions/install-consumer-ci/requirements-consumer-ci.txt` (ruff, mypy,
  pytest installer pins; test-bound copy)
- `presets/typescript/biome.json` `$schema` (frozen preset; test-bound copy)
- `.github/actions/install-semgrep/locks/semgrep-<v>.txt` and
  `.github/actions/install-biome/locks/biome-<v>.json` (hash/digest locks for the
  bytes of the locked version)
- `.github/actions/install-consumer-ci/core-owned-tools.json`
  (`l9.core-owned-tools/v1`) — the **version-free** list of Core-owned tools and
  their dependency identifiers per Dependabot ecosystem (`pip`, `uv`,
  `pre-commit` repository URLs, `npm`). `Quantum-L9/.github` reads it from
  Core `main` at this path to render consumer Dependabot ignore rules.
  Ecosystem `pre-commit` identifiers are repository URLs exactly as written in
  `.pre-commit-config.yaml`; how Dependabot matches them is for the renderer to
  verify.

Repository-owned **config** (`ruff.toml`, `[tool.mypy]`, `biome.json` extras)
stays in the consumer. Versions do not.

## Formatter/linter ownership

Exactly one formatter owns each language; a second formatter for the same
language produces a diff that churns on every save.

| Languages | Owner | How |
|---|---|---|
| `javascript`, `typescript`, `json`, `jsonc` | **Biome** | Core-owned: `install-biome` (digest-locked binary at the lock's version) then `run-biome-check` (`biome ci --reporter=github`, advisory) |
| `python` | **ruff** | `ruff check` + `ruff format --check` after `install-consumer-ci@v2` |

The frozen TypeScript preset and starter still call the SDK reusable workflow
`l9-biome-scan.yml` at a full SHA until they are retired; no new surface does.

## Toolchain alignment (advisory)

`check-toolchain-alignment` reads the consumer checkout (`pyproject.toml`
dependency groups and optional dependencies, `uv.lock`, root
`requirements*.txt`, `.pre-commit-config.yaml` revs, `biome.json`/`biome.jsonc`
`$schema`, `package.json` `@biomejs/biome`) and classifies each Core-owned tool
declaration as `exact_match`, `exact_drift`, `range_admits_lock`,
`range_excludes_lock`, `unpinned`, or `unparseable`. Drift is annotated at
`file:line`; the job never fails on a consumer finding. The report is
`l9.toolchain-alignment/v1` JSON at `.l9/runtime/toolchain-alignment.json`.

## Bumping a tool version

1. One human pin-file PR in Core edits `toolchain-lock.json` and every bound
   copy (the tests fail until all move together), plus a new
   `install-semgrep` / `install-biome` lock file where the bytes change.
2. Merge to `main`: production for `org-ci.yml` on the next governed event.
3. `tools/publish_consumer_ci_tag.sh` moves `v2` for `install-consumer-ci@v2`
   users; it prints the push for a human.

There is deliberately no Dependabot entry for the lock: Dependabot updates
manifests and lock files of supported package ecosystems and `uses:` action
references, not a JSON version map or digest locks.

## Consumer type-check contract (Python / mypy)

- **Required, blocking by default.** `pr-pipeline.yml` `mypy-required` defaults to `true`.
- **Repository-owned configuration.** No global `--ignore-missing-imports`.
- **Pydantic is opt-in** in the consumer's own mypy config.
- **Versions** come from the installer action, not Dependabot, not a copied pin file.

## Adopt it

1. Copy [`templates/l9-lint-test.yml`](./templates/l9-lint-test.yml) or call the
   installer from your existing lint job.
2. Keep the `env:` knobs (`PYTHON_VERSION`, `SOURCE_DIR`, `TEST_DIR`,
   `COVERAGE_THRESHOLD`).
3. Do not add `requirements-consumer-ci.txt` to the consumer.

## Pairing with the analysis pipeline

Pin Core analysis workflows by immutable SHA (never `@main`) unless the
repository and workflow exactly match the bounded optional integration channel
in `.l9/release-plane.yaml`. The installer tag `@v2` is otherwise independent
of the analysis SHA.
