# Repository Execution Runtime

**Artifact:** `l9-ci-core-repository-execution-runtime`

**Version:** `4.3.1`

This local-first runtime compiles repository policy and Git state into bounded
validation, evidence-bearing completion checks, and guarded push/PR operations.
It is a repository-execution layer inside `l9-ci-core`; it does not own SDK
analysis semantics, canonical findings, Assurance decisions, repair planning,
or learning.

## Authority

Authority resolves in this order:

1. `AGENTS.md` and the target `.l9` contracts: `architecture.yaml`,
   `ownership.yaml`, `sdk-compatibility.yaml`, `org-runtime-contract.yaml`,
   and `org-runtime-interface.yaml`.
2. `.l9/repo-workflow.json`.
3. `.l9/repo-workflow.schema.json`.
4. `tools/l9_repo/` runtime behavior.
5. `Makefile`, generated from `tools/l9_repo/Makefile.template`.

`.l9/repo-workflow.json` registers the target authorities under
`authority.target_authorities` and requires `AGENTS.md` to reference each
(`agent_contracts.reference_requirements`); structural validation fails
closed when any registered authority is missing or unreferenced.

## Commands

- `make setup`: install target and runtime validation dependencies.
- `make validate`: validate schema, checksum manifest, authority wiring,
  generated-facade parity, and the configured workflow-integrity command.
- `make change-policy`: display changed files, selected targeted gates, and
  companion obligations.
- `make agent-check`: run structural validation, targeted gates, full check and
  test matrices, prove non-mutation, and emit JSON/Markdown receipts.
- `make status`: report branch, worktree, upstream, ahead/behind, and remote
  freshness.
- `make push` / `make pr`: execute single-flight guarded mutation only after a
  passing completion proof.

Repository-specific targets live in `Repo.mk`. They are release-assurance
helpers, not part of the common facade `tools/l9_repo` owns:

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

Configured command argv (including `change_policy` gate commands and
`push.lockfile_command`) is consumed **argv-only and allowlisted**: `argv[0]`
must be `@python` (the workspace interpreter) or one of the pinned toolchain
`ruff`, `mypy`, `uv`. Any other executable is rejected fail-closed at
configuration load, so a repository contract can never smuggle arbitrary
commands through the runner. Command arguments are passed literally and are
never evaluated by a shell.

## Invariants

- Targeted gates add evidence and never replace the full configured suite.
- Exit `0` is success, `1` is a blocking repository finding, and `2` is invalid
  configuration, infrastructure, comparison context, or repository state.
- Validation must preserve the initial subject, policy digest, index, tracked
  worktree, and untracked-file set.
- Configured commands are allowlisted (`@python`, `ruff`, `mypy`, `uv`) and
  executed argv-only.
- Force push, protected-branch mutation, shell-string command execution, and
  hidden bypasses are prohibited.
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
