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
| Semgrep | SDK | provider run, canonical findings, technical gate | yes: every finding is blocking unless the central policy classifies its rule advisory |

Three things a repository can do, all optional, all declarative:

## 1. Declare the Repository Execution V2 contract (runs your lint, type-check and tests)

Add exactly these three files. Nothing else is read.

`.l9/repo-workflow.json` — the exact three-field declaration:

```json
{
  "schema": "l9.repo-execution/v2",
  "facade": "make-v1",
  "required_phases": ["setup", "validate", "check", "test"]
}
```

`Makefile` — the released `make-v1` facade, byte-for-byte. Generate it from
any Core checkout; never hand-edit it:

```bash
CONSUMER="$(pwd)"                      # your repository root (a Git repository root)
git clone --depth=1 https://github.com/Quantum-L9/l9-ci-core.git /tmp/l9-ci-core
cd /tmp/l9-ci-core                     # the module is importable only from the Core checkout
python3 -m tools.l9_repo --workspace "$CONSUMER" reconcile   # writes $CONSUMER/Makefile
python3 -m tools.l9_repo --workspace "$CONSUMER" validate    # contract + facade + Repo.mk ABI
cd "$CONSUMER"
```

`--workspace` always names the consumer, never the Core clone; `reconcile`
writes only the consumer's `Makefile` and never touches `Repo.mk`.

`Repo.mk` — repository-owned, hand-maintained. It must declare the four
leaves, each `.PHONY`, each with an unconditional recipe or prerequisite, and
must not redefine `setup`, `validate`, `check`, `test`, or `help`:

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
	@$(PYTHON) -m mypy

repo-test: ## The repository test suite
	@$(PYTHON) -m pytest -q
```

A leaf that does nothing (`@true`) passes structurally and proves nothing;
make every leaf run the real command. Versions of `ruff`, `mypy` and `pytest`
are Core-owned: install them through
`Quantum-L9/l9-ci-core/.github/actions/install-consumer-ci@<full-sha>` in a
repository workflow, or pin them in `requirements-ci.txt` to the values in
`.github/actions/install-consumer-ci/toolchain-lock.json` (toolchain
alignment reports any drift).

Enforcement mode is decided by Core, never by the repository
(`.github/actions/run-repository-verification/contract-policy.json`):

- `migration` (default) — an absent contract is `not_applicable` and passes;
  a present contract must pass.
- `required` — the repository is listed in `required_repositories`; an
  absent, invalid, or failing contract fails the required check.

To move a repository to `required`, open a Core PR that adds it to
`required_repositories`; the change takes effect when the
`run-repository-verification` pin in `org-ci.yml` advances to that revision
(`docs/repository-execution-runtime.md`).

## 2. Stamp the locked Biome contract (JavaScript / TypeScript)

```bash
bash /tmp/l9-ci-core/presets/typescript/stamp.sh "$(pwd)"
```

Do not hand-author `biome.json`; see `presets/typescript/README.md`.

## 3. Optional ownership pointer

`.l9/ci.json` (`l9.ci-consumer/v1`) may carry only `owner`, `repo_class`
(`auto`, `python`, `typescript`) and centrally issued `waiver_refs`. It cannot
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
