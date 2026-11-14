# presets

Core-owned consumer configuration, written into a repository by one command
and verified byte-for-byte (`AGENTS.md` §24):

```bash
python3 -m tools.l9_repo --workspace <consumer> stamp --profile <python|typescript|python+typescript>
python3 -m tools.l9_repo --workspace <consumer> validate
```

| Directory | Files |
|---|---|
| `python-tooling/` | `ruff.toml`, `mypy.ini`, `pytest.ini` (see `python-tooling/README.md`) |
| `typescript/` | the locked Biome contract `biome.json`, `.biomeignore`, `.editorconfig` (see `typescript/README.md`) |

`stamp` also writes `.l9/repo-workflow.json`, the `make-v1` `Makefile` and,
for Python, `requirements-ci.txt` rendered from
`.github/actions/install-consumer-ci/toolchain-lock.json`. `typescript/stamp.sh`
remains for keep-if-absent Biome stamping.

The per-language copy-first presets (the old `presets/python`, the preset
workflows and governance packs) implemented the old copy-first CI model and
were retired on 2026-10-22 (`AGENTS.md` §15); `python-tooling/`
holds configuration files only, never a workflow. GitHub organization rulesets
require [`.github/workflows/org-ci.yml`](../.github/workflows/org-ci.yml)
directly. Do not copy `.github/workflows/l9-analysis.yml` or
`.github/governance/` into a consumer, and do not distribute CI from
`Quantum-L9/.github`.
