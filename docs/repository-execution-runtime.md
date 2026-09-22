# Repository Execution Runtime

**Artifact:** `l9-ci-core-repository-execution-runtime`

**Version:** `5.0.0`

This local-first runtime compiles repository policy and Git state into bounded
validation, evidence-bearing completion checks, status, cleanup, and
generated-facade integrity for `l9-ci-core`. It is a repository-execution layer
inside `l9-ci-core`; it does not own SDK analysis semantics, canonical findings,
Assurance decisions, repair planning, or learning.

**It does not own Git publication.** Branch publication policy, protected-branch
publication denial, push semantics, pull-request creation and reuse, publication
single-flight and overlap policy, and publication remediation belong to
Cursor-Governance. The generated Make facade delegates those operations to the
installed `l9` dispatcher.

## Authority

Authority resolves in this order:

1. `AGENTS.md` and the target `.l9` contracts: `architecture.yaml`,
   `ownership.yaml`, `sdk-compatibility.yaml`, `org-runtime-contract.yaml`,
   and `org-runtime-interface.yaml`.
2. `.l9/repo-workflow.json`.
3. `.l9/repo-workflow.schema.json`.
4. `tools/l9_repo/` runtime behavior.
5. `Makefile`, generated from `tools/l9_repo/Makefile.template`.
6. `Repo.mk`, the repository-owned implementation boundary.

`.l9/repo-workflow.json` registers the target authorities under
`authority.target_authorities` and requires `AGENTS.md` to reference each
(`agent_contracts.reference_requirements`); structural validation fails
closed when any registered authority is missing or unreferenced.

## The two-file facade

The root `Makefile` is generated and portable. It owns the operator vocabulary
and implements nothing:

- **Repository verbs** (`setup`, `validate`, `check`, `test`, `clean`,
  `doctor`) delegate to the `repo-*` leaves in `Repo.mk`.
- **Governance verbs** (`start`, `pr`, `workspace-clean`, `wiring-check`)
  delegate to the `l9` dispatcher, which resolves `CONSUMER_SAFE` targets
  against the Cursor-Governance Makefile.

`include Repo.mk` is mandatory, not `-include`: a missing implementation
boundary is a broken repository, not a silent degrade. Because that makes the
whole Makefile unparseable, the recovery path bypasses `make` entirely:

```bash
python3 -m tools.l9_repo reconcile
```

`Repo.mk` may implement repository capabilities. It may not implement
organization governance, and it defines no `pr` target and no `push` target.

## Commands

- `make setup`: install target and runtime validation dependencies.
- `make validate`: validate schema, checksum manifest, authority wiring,
  generated-facade parity, and the configured workflow-integrity command.
- `make check`: run the configured static/quality matrix (`ruff check`,
  `ruff format --check`, `mypy`).
- `make test`: run the configured test matrix.
- `make doctor`: verify the local execution toolchain. This checks only what
  this runtime needs to execute; GitHub reachability and credential state are
  publication concerns and are neither required nor probed here.
- `make clean`: remove the configured disposable outputs.
- `make change-policy`: display changed files, selected targeted gates, and
  companion obligations.
- `make agent-check`: run structural validation, targeted gates, full check and
  test matrices, prove non-mutation, and emit JSON/Markdown receipts.
- `make status`: report branch, sha, worktree state, comparison ref, ahead/behind,
  and remote freshness. It reports no pull-request state: that lives on the
  publication plane, and aggregating the two belongs to Cursor-Governance.
- `make reconcile`: regenerate the root Makefile from the canonical template.
- `make wiring-check`, `make start`, `make workspace-clean`, `make pr`: delegate
  to Cursor-Governance through the `l9` dispatcher.

Repository-specific targets live in `Repo.mk`. The release-assurance helpers are
not part of the common facade:

- `make check-release-writers`: run `tools/check_release_writers.py`, which
  proves exactly one authorized executable surface can mutate the exact
  `vX.Y.Z` Core release namespace and one the transitional `v2` installer tag,
  and that neither can write the other's. The same invariant runs inside the
  `unittest` suite, so the release gate enforces it too.
- `make attest-control-plane`: run `tools/verify_control_plane.py`, a
  read-only comparison of live GitHub state against `.l9/release-plane.yaml`
  (organization required-workflow binding, Core `main` protection, immutable
  releases). It issues only `GET` requests, reads a credential from
  `L9_CONTROL_PLANE_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`, and exits non-zero
  unless every check is `PASS` — a state it cannot determine is `UNKNOWN`,
  never `PASS`. See `docs/release/README.md`.

Evidence is written under `artifacts/`, which remains untracked.

Configured command argv (including `change_policy` gate commands) is consumed
**argv-only and allowlisted**: `argv[0]` must be `@python` (the workspace
interpreter) or one of the pinned toolchain `ruff`, `mypy`, `uv`. Any other
executable is rejected fail-closed at configuration load, so a repository
contract can never smuggle arbitrary commands through the runner. Command
arguments are passed literally and are never evaluated by a shell.

## The check gate resolves its toolchain, then proves it

`commands.check` runs ruff and mypy as `@python -m <tool>`, not as bare names.
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
`@python` settles it: the interpreter, not `PATH`, selects the code.

Two residues remain, and `tools/check_toolchain_versions.py` — the first entry
in `commands.check`, and the first step of the CI lint job — closes both.

The interpreter itself may have been provisioned from something other than the
pin files, so the check compares `importlib.metadata.version()` for ruff and
mypy against `.github/actions/install-consumer-ci/toolchain-lock.json`.

Metadata alone is not enough, because it proves what is *installed* rather
than what `-m` will *import*. `-m` puts the working directory at the front of
`sys.path`, so a plain `ruff/` package at the repository root would win for
the gate while metadata still reported the pinned version — the same shadowing
defect, moved from `PATH` to `sys.path`. The check therefore also resolves
each module under the gate's own search path and confirms it belongs to the
pinned distribution.

The same contract applies in CI: `self-ci.yml`'s lint job runs the preflight
first and invokes `python -m ruff` / `python -m mypy`. Installing the pins and
executing them are different things, and the lint job is what gates a merge.

The lock is the canonical owner of tool versions. The preflight reads it and
never declares a version, and `tests/actions/test_install_consumer_ci.py`
binds every other copy — the installer pins, `requirements-repo-runtime.txt`,
the pre-commit `rev`, the Biome schema, and inline workflow `pytest` literals
— back to it. Bump the lock, and every other pin must follow.

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

## Comparison ref, and the deprecated publication keys

`pull_request.base` is still read, in exactly one place and for one reason: it
names the branch this runtime compares against — the diff base for
`change-policy` and `agent-check`, and the fallback comparison ref for
`status`. That is a repository fact wearing a publication-shaped name.

Every other key in `push` and `pull_request` is **deprecated and dead**: no
code path reads it. They are still declared, and still validated, because the
contract's *shape* is co-versioned with the Core runtime pinned by
`.github/workflows/org-ci.yml`:

```yaml
uses: Quantum-L9/l9-ci-core/.github/actions/run-repository-verification@<sha>
```

That pinned checkout parses this repository's `.l9/repo-workflow.json` with
*its own* validator, which still requires `push` and `pull_request` and rejects
an unknown `repository.default_branch`. Removing the blocks, or adding
`default_branch`, therefore fails organization CI against the current pin —
a self-hosting bootstrap constraint, not a design preference.

**Removal trigger:** once a Core release whose runtime tolerates their absence
is pinned in `org-ci.yml`, drop both blocks and replace `pull_request.base`
with `repository.default_branch`. `tests/tools/test_l9_repo_facade_boundary.py`
asserts the current state, so that removal fails a test rather than happening
silently.

## Invariants

- Targeted gates add evidence and never replace the full configured suite.
- Exit `0` is success, `1` is a blocking repository finding, and `2` is invalid
  configuration, infrastructure, comparison context, or repository state.
- Validation must preserve the initial subject, policy digest, index, tracked
  worktree, and untracked-file set.
- Configured commands are allowlisted (`@python`, `ruff`, `mypy`, `uv`) and
  executed argv-only.
- The check gate resolves ruff and mypy through `@python -m`, so `PATH` cannot
  decide which code it runs, and asserts the resolved versions against the
  canonical lock before any of them execute.
- Shell-string command execution and hidden bypasses are prohibited.
- Single-flight locking guards `reconcile`, the one remaining mutating target.
- The generated facade holds no Git publication logic and no GitHub API logic.
- `MANIFEST.sha256` must be regenerated for every tracked change.
- Two surfaces verify it: `make validate` via the repository facade, and
  `tests/tools/test_manifest_integrity.py` on the pull-request path, because
  `self-ci.yml` and `governance-ci.yml` run `unittest discover` and never
  invoke the facade. Without the test, a dependency bump or docs edit that
  skipped the manifest merged green and only failed later on someone's local
  `make validate` or in Phase 4 release validation — which is how #81 and #82
  left `main` unable to pass `make validate`.
- `L9_MANIFEST_CHECK=0` disables both, for bisects and salvage work on a
  knowingly drifted tree. While disabled the manifest is recorded but
  unverified and provides no tamper-detection, so keep the window to the single
  command that needs it.
