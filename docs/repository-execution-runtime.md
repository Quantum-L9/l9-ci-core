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

## Comparison ref

`repository.default_branch` is the repository fact this runtime compares
against: it is the diff base for `change-policy` and `agent-check`, and the
fallback comparison ref for `status`. It is validated as a member of
`repository.protected_branches`. It is not publication policy, and it replaced
`pull_request.base`, which carried that comparison responsibility alongside
pull-request creation before publication moved to Cursor-Governance.

## Invariants

- Targeted gates add evidence and never replace the full configured suite.
- Exit `0` is success, `1` is a blocking repository finding, and `2` is invalid
  configuration, infrastructure, comparison context, or repository state.
- Validation must preserve the initial subject, policy digest, index, tracked
  worktree, and untracked-file set.
- Configured commands are allowlisted (`@python`, `ruff`, `mypy`, `uv`) and
  executed argv-only.
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
