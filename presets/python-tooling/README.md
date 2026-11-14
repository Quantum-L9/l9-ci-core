# Core Python preset (ruff / mypy / pytest)

Core-owned standalone configuration files for Python consumers. A repository
never edits them: `stamp` writes them and `validate` proves them
byte-identical to the Core revision they came from (`AGENTS.md` §24).

| File | Tool | Why a standalone file |
|---|---|---|
| `ruff.toml` | Ruff | A `ruff.toml` makes Ruff ignore `[tool.ruff]` in `pyproject.toml`. |
| `mypy.ini` | mypy | mypy reads `mypy.ini` first and never merges configuration files. |
| `pytest.ini` | pytest | pytest uses the first configuration file it finds; `pytest.ini` wins. |

Core never writes or edits a consumer's `pyproject.toml`; any `[tool.ruff]`,
`[tool.mypy]` or `[tool.pytest.ini_options]` table left there is ignored by the
tool once the preset is stamped.

## Policy and where it comes from

| Setting | Value | Source |
|---|---|---|
| Ruff `line-length` | 100 | `Quantum-L9/l9-repo-template` (the birth template, a required consumer) |
| Ruff `target-version` | `py312` | `toolchain-lock.json` `python` (3.12.x) |
| Ruff `select` / `ignore` | `E F B I UP` / `E501` | `l9-repo-template`; a superset of Core's own `E4 E7 E9 F` |
| mypy | `strict = True`, `python_version = 3.12` | L9 standard; `l9-repo-template` runs strict |
| pytest | `testpaths = tests` | `l9-repo-template` |

Tool versions are not here: `requirements-ci.txt` is rendered from
`.github/actions/install-consumer-ci/toolchain-lock.json` by the same stamp.

## Consequences for a repository

- Name checked paths in `Repo.mk`: `python -m mypy src tests`.
- An untyped third-party import is silenced inline
  (`# type: ignore[import-untyped]`); per-module mypy overrides cannot live in
  `pyproject.toml`.
- Register pytest markers with `@pytest.mark` usage only; `--strict-markers`
  is not set.

## Stamp

```bash
git clone https://github.com/Quantum-L9/l9-ci-core.git /tmp/l9-ci-core
git -C /tmp/l9-ci-core checkout <revision org-ci.yml pins>
cd /tmp/l9-ci-core
python3 -m tools.l9_repo --workspace "$CONSUMER" stamp --profile python
python3 -m tools.l9_repo --workspace "$CONSUMER" validate
```

Changing a preset is a Core PR; consumers re-stamp from the new revision once
`org-ci.yml`'s pins advance to it.
