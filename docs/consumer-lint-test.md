# Consumer lint + test workflow

Generic ruff / mypy / pytest versions are **Core-owned**. Callers install them
with the composite action; they do not copy pin files and Dependabot does not
bump those packages.

```yaml
- uses: Quantum-L9/l9-ci-core/.github/actions/install-consumer-ci@v2
```

`@v2` is a floating major tag moved only after a human Core pin-file PR, by
`tools/publish_consumer_ci_tag.sh`. It is the consumer toolchain installer
tag only: it is **not** a Core release alias (Core releases never move it,
see `.l9/release-plane.yaml`) and it is **not** a governed
organization-enforcement path (that is Core `main` via the organization
ruleset). Analysis / SDK invoke stays SHA-pinned. Never `@main`.

Authority files in Core:

- `.github/actions/install-consumer-ci/requirements-consumer-ci.txt` (ruff, mypy, pytest)
- `presets/typescript/biome.json` `$schema` (Biome)
- `.github/actions/install-consumer-ci/toolchain-lock.json` (derived lock)

Repository-owned **config** (`ruff.toml`, `[tool.mypy]`, `biome.json` extras)
stays in the consumer. Versions do not.

## Formatter/linter ownership

Exactly one formatter owns each language; a second formatter for the same
language produces a diff that churns on every save.

| Languages | Owner | How |
|---|---|---|
| `javascript`, `typescript`, `json`, `jsonc` | **Biome** | SDK reusable workflow `l9-biome-scan.yml` at a full SHA |
| `python` | **ruff** | `ruff check` + `ruff format --check` after `install-consumer-ci@v2` |

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

Pin Core analysis workflows by immutable SHA (never `@main`). The installer
tag `@v2` is independent of that SHA.
