# L9 CI Debt Organism — Layer 1–4 run, 2026-09-07

Preserved evidence from a nine-repo organism verification run. Stored here so the receipts survive
the ephemeral workspace they were produced in (`/tmp/l9-organism`), which is reclaimed with the
session.

**Decision: `do-not-deploy` (organism status `fail`).**

## What this run does and does not contain

| Layer | Executed | Receipt |
|---|---|---|
| Layer 1 — repo health | **yes** | `evidence/01-repo-health.json` |
| Layer 2 — seam tests | **no** | — |
| Layer 3 — corridor tests | **no** | — |
| Layer 4 — whole-organism aggregation | **yes** | `evidence/04-organism-receipt.json` |

Layers 2 and 3 were not run. No `02-seam-tests.json` or `03-corridor-tests.json` is included, and no
placeholder stands in for them: Layer 4 aggregates evidence and may not create it. Every seam,
corridor, and fail-closed negative test is therefore recorded as `missing`, not as passing.

The organism is **unproven**, not proven-broken. That distinction is the point of the run.

## Scope

Nine repositories, all present and recorded at their `origin/main` tips with clean git trees:

`l9-ci-debt-intelligence`, `l9-ci-debt-lsp`, `l9-ci-debt-resolver`, `l9-ci-core`, `l9-ci-sdk`,
`l9-assurance`, `l9-harness`, `l9-pr-repair`, `l9-observability-core`.

Out of scope: `l9-constellation-topology`.

Repository identity: the `PR_Repair` → `l9-pr-repair` rename needed no normalization — Layer 1
recorded the repository as `l9-pr-repair` directly, at canonical remote
`https://github.com/Quantum-L9/l9-pr-repair`, and its head commit is itself the rename stamp (#63).
Reconciliation status `pass`.

## Layer 1 result: 9/9 install, 6/9 native health

Three non-zero health results, which are **not** three repository defects:

1. **`l9-ci-core` — a real finding in this repository.** `make check` passes ruff and ruff-format,
   then mypy reports 4 `Library stubs not installed` errors — `yaml` in
   `tools/check_release_writers.py`, `.github/actions/provision-sdk/provision.py` and
   `tools/verify_control_plane.py`, and `jsonschema` in `tools/l9_repo/__main__.py`. `types-PyYAML`
   is declared in `requirements-ci.txt`, but mypy runs under `$PYTHON=python3` rather than the
   environment that declaration was installed into; `types-jsonschema` is not declared anywhere in
   the repo. This is a dependency-declaration gap, and it is this repository's to fix.
2. **`l9-ci-sdk` — sandbox limitation, health undetermined.** `make ci` → `make hooks` runs zizmor,
   which fetches `github.com/actions/checkout.git` and receives HTTP 401. The test suite was never
   reached, so this repo's health is undetermined rather than failing.
3. **`l9-ci-debt-intelligence` — harness contamination, not a repo defect.** 258 of 259 tests pass.
   The single failure is a publication-boundary invariant correctly flagging
   `.venv/lib/python3.11/site-packages/pip/_vendor/certifi/cacert.pem` — a file the Layer 1 harness
   itself created by running `ensurepip` into the repo's gitignored `.venv` to repair a pip-less
   venv left by the session-deps hook. The repo's invariant is sound.

### Known contamination in the producing workspace

The Layer 1 harness ran `ensurepip` into four repo-local gitignored `.venv` directories
(`l9-ci-debt-intelligence`, `l9-ci-debt-lsp`, `l9-ci-debt-resolver`, `l9-ci-core`). Removal was
attempted and **denied by the operator permission gate**, so the contamination persisted in that
workspace. It affects only `l9-ci-debt-intelligence`'s single failing assertion, and it touched no
tracked file in any repository — all nine git trees stayed clean throughout.

## Mutation posture

Layers 1 and 4 edited no repository source, changed no schema or registry, enabled no `l9-pr-repair`
mutation path, and performed no remote mutation. This directory is the only repository change the
run produced, and it is additive evidence.

## Which document is authoritative

`evidence/04-organism-receipt.json` is the machine-readable source of truth for this run, and
`evidence/deploy-summary.md` is the Layer 4 contract's own human-readable rendering of it — both are
generated output and neither is hand-edited. This `REPORT.md` is a hand-written index following the
`docs/pipeline-runs/` convention of this repository. Where they appear to disagree, the receipt wins
and this file is stale.

Digests for every generated artifact are in `evidence/layer-4/logs/layer-4-output-digests.json`
(15 files, all verified against the copies stored here). That manifest deliberately does not list
itself.

## Layout

```
REPORT.md                                  this file
evidence/01-repo-health.json               Layer 1 receipt
evidence/04-organism-receipt.json          Layer 4 whole-organism receipt
evidence/deploy-summary.md                 Layer 4 human-readable summary
evidence/layer-4/receipts/*.json           12 Layer 4 sub-receipts
evidence/layer-4/logs/*.json               SHA-256 digests of Layer 4 outputs
evidence/layer-1/package-versions.json     per-repo package name/version
evidence/repo-state/*.txt                  per-repo remote, branch, SHA, commit date, dirty status
evidence/logs/*.log                        raw install and health logs for all nine repos
```

## Next step

Run Layer 2 (seam tests), then Layer 3 (corridor tests), then re-run Layer 4 against all three. Until
those exist, `do-not-deploy` is the only verdict the evidence supports.
