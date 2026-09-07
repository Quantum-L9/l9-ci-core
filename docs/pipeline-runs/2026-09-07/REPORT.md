# L9 CI Debt Organism — Layer 1–4 run, 2026-09-07

Preserved evidence from a nine-repo organism verification run. Stored here so the receipts survive
the ephemeral workspace they were produced in (`/tmp/l9-organism`), which is reclaimed with the
session.

**Decision: `do-not-deploy` (organism status `fail`).**

## What this run does and does not contain

| Layer | Executed | Receipt |
|---|---|---|
| Layer 1 — repo health | **yes** | `evidence/01-repo-health.json` |
| Layer 2 — seam tests | **yes** | `evidence/02-seam-tests.json` |
| Layer 3 — corridor tests | **yes** | `evidence/03-corridor-tests.json` |
| Layer 4 — whole-organism aggregation | **yes** | `evidence/04-organism-receipt.json` |

**Layer 3 result: 3 of 6 corridors pass, 3 skipped, 0 fail.**

| Corridor | Status | Meaning |
|---|---|---|
| `ci_evidence` | **pass** | Core-driven SDK evidence reaches Assurance in one run, digest-linked |
| `assurance_harness` | **pass** | Harness invokes Assurance without authority confusion |
| `observability_contracts` | **pass** | Digest determinism holds; malformed input fails closed |
| `learning_feedback` | skipped | required seam `resolver_to_intelligence` failed |
| `editor_advisory` | skipped | required seam `intelligence_to_lsp` partial |
| `standalone_repair_safety` | skipped | required seam `pr_repair_standalone` partial |

`ci_evidence` was produced by a **fresh chain**, not by reusing Layer 2 artifacts — reusing them
would not prove same-run continuity. `assurance_harness` then consumed that same observation, so the
two passing corridors compose on one artifact. Continuity is digest-linked end to end: the
observation's `artifacts[0].digest` equals `FindingBundle.canonical_digest()`, and Assurance's
`evidenceId` derives from the `observationId`.

A skipped corridor is **not** a pass, so Layer 3 is `partial`. None was forced with a hand-authored
artifact.

**Layer 2 result: 4 of 7 active seams pass, 2 partial, 1 fail.** No seam was forced with a
hand-authored artifact, and every non-pass is a *blocked producer*, not a broken path:

| Seam | Status | Evidence / blocker |
|---|---|---|
| `core_to_sdk` | **pass** | Core's own `invoke-sdk` action drove the SDK; contract identity 2.0.0 verified |
| `sdk_to_assurance` | **pass** | Real SDK observation admitted — accepted 1, rejected 0 |
| `resolver_to_intelligence` | **fail** | `REDIRECT_AUTH_FORWARDING` — a real defect in l9-ci-debt-resolver (see below) |
| `intelligence_to_lsp` | partial | `PublicationGateError` — no promotion-eligible candidates in a 0-finding corpus |
| `harness_to_assurance` | **pass** | Real Assurance invoked; `authoritative: false` recorded |
| `pr_repair_standalone` | partial | `SURFACE_UNSUPPORTED_GRAPHQL` — 403 on live review ingest |
| `observability_contracts` | **pass** | Deterministic digest; malformed input rejected |

**The primary bloodstream seam is closed.** The original audit found SDK→Assurance broken — Assurance
rejected the SDK's `artifact.sdkVersion` and producer trust was inactive. Both now behave correctly:
the real field is accepted, and an out-of-range version is rejected as
`EVIDENCE_PRODUCER_VERSION_REVOKED`.

**9 of 15 fail-closed negative tests ran and passed; none failed.** The 6 not-run sit behind a
blocked producer, not a skipped check.

### Retracted: the resolver blocker is a defect, not a missing credential

An earlier revision of this run recorded `resolver_to_intelligence` as *partial* because "this surface
holds no GitHub credential". **That was wrong.** The credential works: a direct REST call and a plain
`urllib.request.urlopen` carrying `$GH_TOKEN` both return 200 against the same Actions endpoints.

The real cause is in `l9-ci-debt-resolver`: `providers/github/transport.py` re-sends the
`Authorization` header across GitHub's 302 redirect from the job-logs endpoint to signed Azure Blob
Storage, which rejects it with 401 — and the transport maps any 401 to `AuthenticationError`, so a
redirect bug surfaces as a credential failure.

Proof, same token and endpoint: `curl -L` → **200**; `curl -L --location-trusted` → **401**;
`urllib.urlopen` → **401**. With the header stripped at runtime, acquisition completes (93,158-byte
redacted log, `terminal_state: evidence_ready`), so this is the *sole* blocker of log acquisition.
GitHub always redirects job logs to blob storage, so this fails in CI too — it is not a sandbox
artifact. Fix: strip `Authorization` when a redirect crosses hosts, as curl and requests both do.

The seam then stops at a second, *correct* gate: `SnapshotMismatchError`, because completing it needs
a failed CI run and an SDK bundle at the same revision, and no failed run exists at any revision
checked out here.

### On GitHub Actions secrets

A secret "wired into" a repository cannot be handed to a local tool: the Actions secrets API is
write-only by GitHub's design, and this proxy additionally returns 403 for `/actions/secrets`. Such a
secret is only ever materialised inside a workflow run.

Everything descends from one real artifact: a semgrep 1.176.1 scan of `Quantum-L9/l9-pr-repair`
@ `f5773d4` (94 files) with the SDK's packaged L9 ruleset, normalised by SDK code. That bundle
carries **zero findings** — the packaged ruleset and semgrep's `p/python` registry ruleset both
returned 0 on real organism code (654 files scanned organism-wide). The finding-carrying path is
therefore proven structurally, not with non-zero findings — and that is precisely what starved the
Intelligence→LSP seam.

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

## Layer 1 result: 9/9 install, 7/9 native health, zero repository defects

Two non-zero health results, and **neither is a repository defect**:

1. **`l9-ci-sdk` — sandbox limitation, health undetermined.** `make ci` → `make hooks` runs zizmor,
   which fetches `github.com/actions/checkout.git` and receives HTTP 401. The test suite was never
   reached, so this repo's health is undetermined rather than failing.
2. **`l9-ci-debt-intelligence` — harness contamination, not a repo defect.** 258 of 259 tests pass.
   The single failure is a publication-boundary invariant correctly flagging
   `.venv/lib/python3.11/site-packages/pip/_vendor/certifi/cacert.pem` — a file the Layer 1 harness
   itself created by running `ensurepip` into the repo's gitignored `.venv` to repair a pip-less
   venv left by the session-deps hook. The repo's invariant is sound.

### Retracted finding: l9-ci-core is clean

The first revision of this run, published as the initial commits of this PR, recorded `l9-ci-core`
as the run's sole repository defect — 4 mypy `Library stubs not installed` errors, with the claim
that `types-jsonschema` was undeclared. **That was wrong, and it was this harness's fault.**

`.github/workflows/self-ci.yml` installs `requirements-ci.txt` **and**
`requirements-repo-runtime.txt`; the latter declares `jsonschema`, `types-jsonschema`, `mypy` and
`ruff`. The Layer 1 driver installed only the first file, so `mypy` resolved from outside the venv
and could not see either stub package. With both installed, `make check` exits 0 — ruff clean, 105
files formatted, `Success: no issues found in 23 source files`.

This repository's own green **Lint and Type Check** on PR #150 is what exposed the contradiction; the
driver now installs every requirements file a repo's own gate declares. `l9-ci-core`'s Layer 1 health
is `pass`, and this run found **no defect in any of the nine repositories**.

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

One caveat on the raw logs: committing them ran this repository's `end-of-file-fixer` and
`trailing-whitespace` hooks, which normalised trailing whitespace and final newlines in
`evidence/logs/l9-ci-core-install.log`, `evidence/logs/l9-ci-debt-intelligence-health.log` and
`evidence/layer-1/package-versions.json`. The change is whitespace-only (`git diff
--ignore-all-space` is empty) and none of those three files is covered by the digest manifest, so no
recorded digest is invalidated.

Digests for every generated artifact are in `evidence/layer-4/logs/layer-4-output-digests.json`
(15 files, all verified against the copies stored here). That manifest deliberately does not list
itself.

## Layout

The complete evidence tree, mirrored without filtering: every receipt, payload, log and digest the
run produced. An earlier revision of this PR carried only a JSON/size-filtered subset, which silently
dropped real artifacts (`results.sarif`, the observability digests, the harness stdout/stderr, the
acquired CI evidence bundle and the Intelligence append-only ledger). That is fixed here.

```
REPORT.md                            this file
evidence/
  01-repo-health.json                Layer 1 receipt
  02-seam-tests.json                 Layer 2 receipt
  03-corridor-tests.json             Layer 3 receipt
  04-organism-receipt.json           Layer 4 whole-organism receipt
  deploy-summary.md                  Layer 4 human-readable summary
  layer-1/
    package-versions.json            per-repo package name and version
    logs/*.log                       install and native-health logs for all nine repos
  layer-2/
    inactive-by-design.json          planned / not-live seam declarations
    receipts/*.json                  7 seam receipts
    payloads/                        every artifact each seam produced or consumed: the SDK
                                     FindingBundle, results.sarif, the mandatory-findings
                                     observation, Assurance admission receipts and their negative
                                     mutations, the Intelligence append-only ledger and snapshot
                                     partition, the acquired CI evidence bundle, pr-repair's
                                     dry-run outputs
    logs/*.log                       per-seam execution logs
  layer-3/
    corridor-inventory.json          which corridors were ready vs skipped, and why
    non-live-corridors.json          corridors deliberately not tested as live
    receipts/*.json                  6 corridor receipts (3 pass, 3 skipped)
    payloads/                        the fresh ci_evidence chain, harness invocation records with
                                     stdout/stderr, observability digests
    logs/*.log                       per-corridor execution logs
  layer-4/
    receipts/*.json                  12 Layer 4 sub-receipts
    logs/*.json                      SHA-256 digests of Layer 4 outputs
    summaries/deploy-summary.md      canonical copy of the summary
  repo-state/*.txt                   per-repo remote, branch, SHA, commit date, dirty status
```

### On the included CI log

`layer-2/payloads/resolver_to_intelligence/evidence-bundles/` holds a real 92KB GitHub Actions job
log, acquired from `Quantum-L9/l9-ci-core` run `33993215464` — the same repository this PR targets.
It is the resolver's own redacted output (`UNIX_PATH` redaction applied) and was scanned before
inclusion: no token, key, bearer, private key or credential assignment appears in it, and every long
string in it is a hex hash. It is here because it is the artifact proving log acquisition works once
the redirect defect is fixed.

## Next step

Layers 1 through 4 have all run. Two things stand between this and a deployable verdict:

1. **Merge `Quantum-L9/l9-ci-debt-resolver#52`**, then re-run Layer 2. That fixes the one real defect
   this run found and should move `resolver_to_intelligence` from fail to pass, which in turn
   unblocks the `learning_feedback` corridor.
2. **Get a non-zero finding through the organism.** Every real scan here returned zero findings, so
   the finding-carrying path is proven structurally but never exercised with actual findings — and
   that emptiness is what starved `intelligence_to_lsp` and, through it, `editor_advisory`.

Until then `do-not-deploy` is the only verdict the evidence supports.
