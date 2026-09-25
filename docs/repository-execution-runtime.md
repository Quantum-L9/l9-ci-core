# Repository Execution Runtime

This document describes Repository Execution V2 as implemented in
`tools/l9_repo/`: the portable consumer contract, the versioned `make-v1`
facade, the repository-owned `Repo.mk`, and Core's own local policy layer. It
is a repository-execution layer inside `l9-ci-core`; it does not own SDK
analysis semantics, canonical findings, Assurance decisions, repair planning,
or learning.

**It does not own Git publication.** Branch publication policy, protected-branch
publication denial, push semantics, pull-request creation and reuse, publication
single-flight and overlap policy, and publication remediation belong to
Cursor-Governance. Nothing in the generated facade, `Repo.mk`, or
`tools/l9_repo` implements or proxies them.

## The consumer contract

`.l9/repo-workflow.json` is exactly this, for every consumer including Core:

```json
{
  "schema": "l9.repo-execution/v2",
  "facade": "make-v1",
  "required_phases": ["setup", "validate", "check", "test"]
}
```

It carries no commands, no policy, and no metadata. Validation is standard
library only and fails closed on any additional field, another `schema`, a
facade that is not a released one, or a `required_phases` list that is not
exactly `setup, validate, check, test` in that order. The organization
admission bridge (`.github/actions/run-repository-verification/run.py`) and
`tools/l9_repo/contract.py` apply the same rule and are tested against the
same rejection cases.

## The Make ownership model

```text
Core-owned generated Makefile (released facade make-v1)
    setup    -> repo-setup
    validate -> repo-validate
    check    -> repo-check
    test     -> repo-test
                    │
                    ▼
repository-owned Repo.mk
    repo-setup, repo-validate, repo-check, repo-test  (required, .PHONY)
    ... any repository-local targets
```

- **`Makefile`** is generated: the bytes of the released facade named by the
  contract, reproduced byte-for-byte. It declares exactly `help` plus the four
  phases, includes `Repo.mk` by ordinary composition, and contains no
  publication or Governance target and no dispatcher variable. Regenerate it
  with `python3 -m tools.l9_repo reconcile`; that command is one-directional
  (facade specification to `Makefile`), idempotent, and never writes
  `Repo.mk`.
- **`Repo.mk`** is repository-owned: hand-maintained, never generated, never
  carrying generated provenance, never requiring an extension file. It must
  declare the four `repo-*` leaves, each `.PHONY` so a same-named file can never
  suppress a phase, and may not redefine a facade verb. Everything else in it
  is the repository's business. Core does not parse GNU Make; it proves only
  those structural properties.
- **`make-v1` is a real version.** `tools/l9_repo/facades/make-v1.mk` is mapped
  explicitly by `FACADE_TEMPLATES` in `tools/l9_repo/contract.py`. Once
  released, its behavior is immutable; an incompatible facade change is a new
  entry, `make-v2`, never a silent mutation.

The former capability-plan compiler (`tools/l9_make`), its generated `Repo.mk`,
the `Repo.local.mk` extension layer, and the `Makefile.template` projection are
retired. There is one Make authority.

## Core's own Repo.mk

Core implements the four leaves with the commands the retired V1 command
matrices ran, in the same order:

| Phase | Commands |
|---|---|
| `setup` | `pip install -r requirements-ci.txt`, `pip install -r requirements-repo-runtime.txt` |
| `validate` | `tools.l9_repo validate`, `tools.l9_repo core-validate`, `tools/check_workflow_integrity.py` |
| `check` | `tools/check_toolchain_versions.py`, `ruff check .`, `ruff format --check .`, `mypy` |
| `test` | `unittest discover --start-directory tests --pattern 'test_*.py' --verbose` |

Every command runs through `$(PYTHON)`; see "The check gate resolves its
toolchain, then proves it" below. Core-local targets outside the portable ABI
also live in `Repo.mk`: `lint` (alias of `repo-check`), `doctor`, `clean`,
`status`, `change-policy`, `agent-check`, `core-validate`, `reconcile`,
`attest-control-plane`, and `check-release-writers`.

## Core-local policy

`.l9/core-repo-policy.json` (`l9.core-repository-policy/v1`) holds what is
Core's and not a consumer's: the comparison ref, clean paths, the reconcile
lock, targeted change gates and companion rules, agent-contract wiring,
evidence reporting, and authority paths. Gate and companion commands are
consumed **argv-only and allowlisted**: `argv[0]` must be `@python` (the
interpreter running the tool) or one of the pinned toolchain `ruff`, `mypy`,
`uv`. Arguments are passed literally and never evaluated by a shell.

## Commands

- `make setup`: install Core's pinned gate toolchain.
- `make validate`: `validate` (contract, facade drift, `Repo.mk` ABI), then
  `core-validate` (checksum manifest, agent-contract wiring, authority paths),
  then the workflow-integrity checker.
- `make check`: toolchain preflight, `ruff check`, `ruff format --check`,
  `mypy`.
- `make test`: the complete `unittest` suite.
- `make doctor`: verify the local execution toolchain (`git`, `make`) and the
  contract. GitHub reachability and credential state are publication concerns
  and are neither required nor probed.
- `make clean`: remove the configured disposable outputs.
- `make change-policy`: display changed files, selected targeted gates, and
  companion obligations.
- `make agent-check`: run `core-validate`, the targeted gates, then
  `make validate`, `make check`, and `make test` with captured output; prove
  non-mutation of HEAD, policy, and worktree; emit JSON and Markdown evidence
  under `artifacts/` (untracked).
- `make status`: report branch, sha, worktree state, comparison ref,
  ahead/behind, and remote freshness. No pull-request state: that lives on the
  publication plane.
- `make reconcile`: regenerate `Makefile` from the released facade under the
  single-flight lock. Never touches `Repo.mk`.
- `make core-validate`: Core-local structural validation only.
- `make check-release-writers`: run `tools/check_release_writers.py`, which
  proves exactly one authorized executable surface can mutate the exact
  `vX.Y.Z` Core release namespace and one the transitional `v2` installer tag.
- `make attest-control-plane`: run `tools/verify_control_plane.py`, a
  read-only comparison of live GitHub state against `.l9/release-plane.yaml`.
  It issues only `GET` requests, reads a credential from
  `L9_CONTROL_PLANE_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`, and exits non-zero
  unless every check is `PASS`; a state it cannot determine is `UNKNOWN`.

## The V1 compatibility path

`RepositoryWorkflow` in `tools/l9_repo/__main__.py` exists for one caller: the
pinned admission bridge, which delegates a repository still declaring
`schema_version: 1` to it. It runs that contract's `commands` matrices for
`setup`, `validate`, `check`, and `test` argv-only under the same executable
allowlist, and nothing more. The superseded V1 structural validation
(JSON schema, capability plan, generated-adapter parity) is retired with the
compiler it depended on. Core itself is a V2 consumer.

## The check gate resolves its toolchain, then proves it

`repo-check` runs ruff and mypy as `$(PYTHON) -m <tool>`, not as bare names.
That is deliberate. A bare name is resolved by `PATH`, and `PATH` is not the
repository's to control: a contributor with a `uv`- or `pipx`-installed ruff
gets that one, silently, because the pin describes what was *installed* and
`PATH` decides what *executes*.

The failure mode is not always loud. A shadowing mypy of the wrong major
reports missing stubs and someone investigates; a shadowing ruff of a nearby
minor passes cleanly while checking a different file set — this repository
measured 83 files under a shadowed `ruff 0.15.8` against 122 under the pinned
`0.16.1`, both reporting success.

**`make setup` does not fix this.** It installs the pinned versions correctly;
they simply land later on `PATH` than the shadow. Provisioning and resolution
are different problems, and only resolution decides what runs. Routing through
the interpreter settles it: the interpreter, not `PATH`, selects the code.

Two residues remain, and `tools/check_toolchain_versions.py` — the first
recipe line of `repo-check`, and the first step of the CI lint job — closes
both. The interpreter itself may have been provisioned from something other
than the pin files, so the check compares `importlib.metadata.version()` for
ruff and mypy against `.github/actions/install-consumer-ci/toolchain-lock.json`.
Metadata alone is not enough, because it proves what is *installed* rather
than what `-m` will *import*: `-m` puts the working directory at the front of
`sys.path`, so a plain `ruff/` package at the repository root would win for
the gate while metadata still reported the pinned version. The check therefore
also resolves each module under the gate's own search path and confirms it
belongs to the pinned distribution.

The same contract applies in CI: `self-ci.yml`'s lint job runs the preflight
first and invokes `python -m ruff` / `python -m mypy`. The lock is the
canonical owner of tool versions; `tests/actions/test_install_consumer_ci.py`
binds every other copy (installer pins, `requirements-repo-runtime.txt`, the
pre-commit `rev`, the Biome schema, inline workflow `pytest` literals) back to
it, and `tests/workflows/test_workflow_toolchain_pins.py` asserts the
`repo-check` leaf keeps resolving through the interpreter.

## SDK trust bootstrap and runtime closure

`provision-sdk` reads `.l9/sdk-compatibility.yaml` with a bounded standard-library
parser. It does not install PyYAML, or any other package, before Core has selected
an allowlisted full SDK revision. Each supported entry records the SHA-256 of the
selected checkout's `requirements.txt` and one or more Core-owned platform lock
names. Provisioning verifies those checkout bytes, but **never installs that
file**. It creates the action-owned runtime and installs only the selected lock
with pip's `--require-hashes --only-binary :all:` controls.

The generated `l9-ci` launcher re-enters the action-owned venv in Python isolated
mode (`-I`). It does not trust the caller's current directory, `PYTHONPATH`, user
site, or Python environment variables. The verified SDK checkout is exposed by a
data-only `.pth` file inside that venv, so a consumer repository containing its
own `l9_ci/` package cannot shadow the selected immutable SDK. The launcher still
inherits `PATH`; that is intentional because Core's separately hash-locked
Semgrep executable must remain the provider selected by `l9-ci semgrep run`.

The current platform contract is CPython 3.12.14 on Linux x86_64. The composite
action selects that interpreter by a full-SHA `actions/setup-python` edge before
the Python adapter runs. The complete closure is
`.github/actions/provision-sdk/locks/cpython-3.12.14-linux-x86_64.txt`.
Regenerate it deliberately with the Core-owned operator tool:

```bash
python3 .github/actions/provision-sdk/lock_runtime.py \
  --revision <allowlisted-full-sdk-sha>
```

The generator fetches the exact SDK requirements bytes, resolves only wheels for
the declared platform, verifies the selected wheel bytes against PyPI metadata,
and reports the requirements digest to copy into the compatibility entry. A
promotion is two-commit activation: first land the lock, digest binding, and
tests; only a successor commit may change workflow pins to select the new
primitive. The first commit must therefore be inert for already pinned callers.

`tools/check_workflow_integrity.py` recursively scans both
`.github/workflows/**/*.{yml,yaml}` and `.github/actions/**/*.{yml,yaml}`. Every
remote `uses:` edge must be a full lowercase 40-character SHA. Composite actions
must be local leaf adapters and may not nest a remote
`Quantum-L9/l9-ci-core/.github/actions/...` edge; `validate-bundle` is the model
and invokes the verified SDK executable directly through its bundled adapter.

## Admission through the organization workflow

`.github/workflows/org-ci.yml` invokes `run-repository-verification` at an
immutable Core SHA. That bridge reads `.l9/repo-workflow.json` once, runs
`make setup`, `make validate`, `make check`, `make test` for a V2 contract
(stopping at the first failure), delegates a `schema_version: 1` contract to
the bounded V1 path, and records `present` / `status` for the workflow's
existing enforcement step. Adopting V2 in Core changed nothing in that action
or its outputs; Core is simply now verified through the V2 branch of it.

## Invariants

- The portable ABI is exactly `setup`, `validate`, `check`, `test`; `help` is
  informational. Nothing else is part of `make-v1`.
- The generated facade holds no Git publication logic, no GitHub API logic,
  no Governance target, and no dispatcher variable.
- `Repo.mk` is never generated and never rewritten by Core tooling.
- Exit `0` is success, `1` is a blocking repository finding, and `2` is invalid
  configuration, infrastructure, comparison context, or repository state.
- Targeted gates add evidence and never replace the full configured suite.
- Validation must preserve the initial subject, policy digest, index, tracked
  worktree, and untracked-file set.
- Shell-string command execution and hidden bypasses are prohibited.
- Single-flight locking guards `reconcile`, the one mutating target.
- `MANIFEST.sha256` must be regenerated for every tracked change. Two surfaces
  verify it: `make validate` via `core-validate`, and
  `tests/tools/test_manifest_integrity.py` on the pull-request path, because
  `self-ci.yml` and `governance-ci.yml` run `unittest discover` and never
  invoke the facade.
- `L9_MANIFEST_CHECK=0` disables both, for bisects and salvage work on a
  knowingly drifted tree. While disabled the manifest is recorded but
  unverified and provides no tamper-detection, so keep the window to the single
  command that needs it.
- The Repository Execution V2 suite is `tests/repo_execution/`, and the
  `repository-execution` change gate runs it whenever `tools/l9_repo/`,
  `tests/repo_execution/`, `.l9/repo-workflow.json`,
  `.l9/core-repo-policy.json`, `Makefile`, or `Repo.mk` changes.
