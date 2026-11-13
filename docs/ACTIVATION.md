# Activating L9 CI in a Quantum-L9 repository

Nothing is copied into a repository to put it under organization CI. The
GitHub organization ruleset **L9 canonical CI required** already binds the
default branch of every non-archived repository in `Quantum-L9` to
`Quantum-L9/l9-ci-core` / `main` / `.github/workflows/org-ci.yml` and requires
the check `Analyze (central Core)`. A new repository is governed the moment it
exists; a merge to Core `main` is production for its next evaluation
(`.l9/release-plane.yaml`).

What the organization workflow does to a repository, in order:

| Step | Owner | What it checks | Blocks? |
|---|---|---|---|
| toolchain alignment | Core | consumer tool pins vs `toolchain-lock.json`, annotated `file:line` | advisory |
| consumer metadata | Core | optional `.l9/ci.json` (`owner`, `repo_class`, `waiver_refs` only) | fatal if invalid |
| Biome | Core | `biome ci` against the root `biome.json` / `biome.jsonc` | yes once `org-ci.yml` sets `mode: blocking` (`AGENTS.md` §16) |
| repository verification | Core | `.l9/repo-workflow.json` → `make setup → validate → check → test` | yes (`pr_fast`, `merge`) |
| Core stamp | Core | stamped Core-owned files byte-identical to Core (`.l9/core-stamp.json`) | yes in `required` mode, warning in `migration` |
| Semgrep | SDK | provider run, canonical findings, technical gate | yes: every finding is blocking unless the central policy classifies its rule advisory |

## 1. Stamp every Core-owned file (one command)

Every file Core owns in a repository is written by one non-interactive
command from a Core checkout, and proven by a second (`AGENTS.md` §24):

```bash
CONSUMER="$(pwd)"                      # your repository root
git clone https://github.com/Quantum-L9/l9-ci-core.git /tmp/l9-ci-core
git -C /tmp/l9-ci-core checkout <the revision org-ci.yml pins>
cd /tmp/l9-ci-core                     # the module is importable only from the Core checkout
python3 -m tools.l9_repo --workspace "$CONSUMER" stamp --profile python     # or typescript, python+typescript
python3 -m tools.l9_repo --workspace "$CONSUMER" validate
cd "$CONSUMER"
```

| Profile | Files written |
|---|---|
| all | `.l9/repo-workflow.json` (exact V2 contract), `Makefile` (`make-v1` facade), `.l9/core-stamp.json` (stamp record) |
| `python` | + `requirements-ci.txt` (rendered from `toolchain-lock.json`), `ruff.toml`, `mypy.ini` (strict), `pytest.ini` |
| `typescript` | + `biome.json`, `.biomeignore`, `.editorconfig` |
| `python+typescript` | the union |

These files are Core-owned: never edit them; re-stamp from a newer Core
revision instead. `stamp` never writes `Repo.mk`, `pyproject.toml`,
`package.json` or source. Stamp from the revision `org-ci.yml` pins:
organization CI compares the bytes with that revision, so a stamp taken from
a newer, not-yet-pinned Core revision reports drift until the pins advance.

`.l9/core-stamp.json` (`l9.core-stamp/v1`) records the Core repository and
40-character revision, the profile, and the sha256 of each stamped file.
`validate` re-renders every listed file from the Core checkout it runs from
and fails on any byte that differs, a missing file, or a record digest that
does not match.

The `make-v1` facade routes `setup`, `validate`, `check` and `test` to leaves
in `Repo.mk`, which stays repository-owned and hand-maintained. It must
declare the four leaves, each `.PHONY`, each with an unconditional recipe or
prerequisite, and must not redefine `setup`, `validate`, `check`, `test`, or
`help`:

```make
# <repo> repository-owned Make implementation (Repository Execution V2).
PYTHON ?= python3

.PHONY: repo-setup repo-validate repo-check repo-test

repo-setup: ## Install the pinned toolchain and the project
	@$(PYTHON) -m pip install -e . -r requirements-ci.txt

repo-validate: ## Contract and structural validation (no mutation)
	@$(PYTHON) -c "import importlib; importlib.import_module('<package>')"

repo-check: ## Static checks: ruff check, ruff format --check, mypy
	@$(PYTHON) -m ruff check .
	@$(PYTHON) -m ruff format --check .
	@$(PYTHON) -m mypy src tests

repo-test: ## The repository test suite
	@$(PYTHON) -m pytest -q
```

A leaf that does nothing (`@true`) passes structurally and proves nothing;
make every leaf run the real command. `python3 -m tools.l9_repo --workspace
"$CONSUMER" reconcile` still regenerates only the `Makefile`, and
`bash presets/typescript/stamp.sh` still stamps the Biome trio keep-if-absent
for a repository that does not use the full stamp.

Enforcement mode is decided by Core, never by the repository
(`.github/actions/run-repository-verification`):

- `required` — the repository is listed in `contract-policy.json`
  `required_repositories` (the projection of the L9 registry, `AGENTS.md` §17),
  **or** the live `Quantum-L9/.github` `semantics/repository_registry.yaml`
  names it with `lifecycle: current`. An absent, invalid, or failing contract
  fails the required check, and stamp drift blocks.
- `migration` (default for non-members) — an absent contract is
  `not_applicable` and passes; a present contract must pass; stamp drift is a
  warning annotation.

A newly born repository therefore becomes `required` the moment its registry
entry with `lifecycle: current` lands on `.github` `main`: no Core PR and no
pin advance are needed for the mode. If the registry cannot be read, the run
fails as infrastructure rather than guessing a mode. Nothing in the
repository can lower its mode.

## 2. Biome only (JavaScript / TypeScript, without the full stamp)

```bash
bash /tmp/l9-ci-core/presets/typescript/stamp.sh "$(pwd)"
```

Do not hand-author `biome.json`; see `presets/typescript/README.md`.

## 3. Optional ownership pointer

`.l9/ci.json` (`l9.ci-consumer/v1`) may carry only `owner`, `repo_class`
(`auto`, `python`, `typescript`), centrally issued `waiver_refs`, and
`product_kind` (`node` or `dependency`, the Quantum-L9/.github ProductKinds).
A Constellation node behind Constellation.Gate declares `"product_kind":
"node"`; only declared nodes are held to the Gate routing rules
(`AGENTS.md` §21). It cannot
select a workflow, a Core or SDK revision, a provider, a mode, a tool version,
a permission, or a required check; unknown fields fail closed (`AGENTS.md` §3).

## What not to do

- Do not add `.github/workflows/l9-analysis.yml`, `.github/governance/`, or
  any `uses: Quantum-L9/l9-ci-core/.github/workflows/...` caller for
  organization CI. The ruleset already requires `org-ci.yml`; a copied caller
  is a second, ungoverned evaluation (`.l9/architecture.yaml`
  `prohibited_architecture`).
- Do not pin `@main`. The only consumer-facing moving tag is
  `install-consumer-ci@v2`, moved by `tools/publish_consumer_ci_tag.sh` after
  a human pin-file PR.
- Do not expect `Quantum-L9/.github` to seed CI; it distributes no L9
  workflow or governance pack.

## Verifying a repository is governed

```bash
gh api repos/Quantum-L9/<repo>/rules/branches/<default-branch> \
  --jq '.[] | select(.type=="workflows" or .type=="required_status_checks")'
```

The `workflows` rule must name `.github/workflows/org-ci.yml` at
`refs/heads/main` of repository id `1285564308` (`Quantum-L9/l9-ci-core`), and
the `required_status_checks` rule must list `Analyze (central Core)`. Core's
`make attest-control-plane` reads the same state against
`.l9/release-plane.yaml` and reports `PASS`, `FAIL`, or `UNKNOWN`.
