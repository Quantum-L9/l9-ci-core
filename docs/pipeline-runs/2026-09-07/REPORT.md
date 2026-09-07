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

**Layer 3 result: 4 of 6 corridors pass, 2 skipped, 0 fail.**

| Corridor | Status | Meaning |
|---|---|---|
| `ci_evidence` | **pass** | Core-driven SDK evidence reaches Assurance in one run, digest-linked |
| `assurance_harness` | **pass** | Harness invokes Assurance without authority confusion |
| `observability_contracts` | **pass** | Digest determinism holds; malformed input fails closed |
| `learning_feedback` | **pass** | unblocked by the merge; full chain ran end to end |
| `editor_advisory` | skipped | required seam `intelligence_to_lsp` partial |
| `standalone_repair_safety` | skipped | required seam `pr_repair_standalone` partial |

`ci_evidence` was produced by a **fresh chain**, not by reusing Layer 2 artifacts — reusing them
would not prove same-run continuity. `assurance_harness` then consumed that same observation, so the
two passing corridors compose on one artifact. Continuity is digest-linked end to end: the
observation's `artifacts[0].digest` equals `FindingBundle.canonical_digest()`, and Assurance's
`evidenceId` derives from the `observationId`.

A skipped corridor is **not** a pass, so Layer 3 is `partial`. None was forced with a hand-authored
artifact.

**Layer 2 result: 5 of 7 active seams pass, 2 partial, 0 fail.** No seam was forced with a
hand-authored artifact, and every non-pass is a *blocked producer*, not a broken path:

| Seam | Status | Evidence / blocker |
|---|---|---|
| `core_to_sdk` | **pass** | Core's own `invoke-sdk` action drove the SDK; contract identity 2.0.0 verified |
| `sdk_to_assurance` | **pass** | Real SDK observation admitted — accepted 1, rejected 0 |
| `resolver_to_intelligence` | **pass** | was fail; fixed by l9-ci-debt-resolver#52, then completed at a matched revision |
| `intelligence_to_lsp` | partial | `PublicationGateError` — 2 candidates compile, 0 promotion-eligible (recurrence: 1 producer, 1 scope) |
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
carried **zero findings** at that point — the packaged ruleset and semgrep's `p/python` registry
ruleset both returned 0 on real organism code (654 files scanned organism-wide). That is what starved
the Intelligence→LSP seam initially. It was later resolved: a scan at `l9-ci-debt-resolver@2410fae`
yielded `finding_count=1`, the first non-zero finding to traverse the organism (see the post-merge
update below).

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

## Layer 1 result: 9/9 install, 8/9 native health, one real repository finding

Re-verified after the initial run. One repository finding, one harness artifact:

1. **`l9-ci-sdk` — FAIL, a real repository finding.** `make ci` → `make hooks` runs zizmor, which
   flags `secrets: inherit` at `.github/workflows/l9-nightly.yml:20` (rule `secrets-inherit`, medium,
   confidence High, unsuppressed). This was initially misrecorded as a sandbox limitation because the
   sandbox's sentinel `GH_TOKEN` pushed zizmor into online mode, where `github.com` 401'd it before
   the audit ran. See *Layer 1 re-verification* below.
2. **`l9-ci-debt-intelligence` — PASS, no repo defect.** The in-place run showed 258 of 259 because
   this harness's own `ensurepip` had placed a pip-vendored `cacert.pem` inside the repo's gitignored
   `.venv`, tripping a sound publication-boundary invariant. Re-run at the same revision in a clean
   `git archive` tree: **259 passed, 0 failed**. See *Layer 1 re-verification* below.

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


## Update after l9-ci-debt-resolver#52 merged

The redirect defect this run found was fixed and merged (`57cf94e`). Two things followed.

**`resolver_to_intelligence` moved fail → pass, and `learning_feedback` with it.** The acquisition now
completes with the *unmodified* CLI. The second gate — the resolver's `SnapshotMismatchError`, which
correctly refuses an SDK bundle whose revision does not match the evidence — was satisfied by pairing
failed run `33481850033` with a scan of the *same* commit `2410fae622bb`, extracted read-only via
`git archive` rather than by switching a shared clone. The full chain then ran: acquire → feedback
event → publish (`l9.feedback-delivery-receipt/v1`, delivered, 201) → Intelligence `accepted`.
Duplicate delivery deduped to the same `record_id`; `verify-store` reports `valid`. The accepted event
is 2234 bytes carrying `repository_pseudonym` — no raw path, name, branch, log, diff, email or token.

**The first non-zero finding traversed the organism.** That revision yields `finding_count=1`. Every
earlier scan in this run returned zero across 654 files and semgrep's `p/python` registry ruleset, so
until now the finding-carrying path was proven only structurally.

**`intelligence_to_lsp` remains partial, but the diagnosis has changed.** It was starved by an empty
corpus; that is no longer true. With a real finding ingested the compiler now yields
`candidate_count: 2` (up from 0) — but `promotion_eligible_count: 0`, because recurrence shows
`distinct_producer_count: 1` and `distinct_scope_count: 1`. Promotion requires a pattern recurring
across producers or scopes; one sighting in one repository at one revision cannot earn a prevention
rule. That is the publication gate working as designed, not a defect. `editor_advisory` stays skipped
behind it.


## Layer 1 re-verification (later in the run)

Both Layer 1 non-passes were re-tested rather than left as first impressions. They resolved in
**opposite** directions, and the original receipt was wrong about each.

**`l9-ci-debt-intelligence` — not a defect, now passing.** The single failing test was tripped by a
`cacert.pem` that this harness's own `ensurepip` put inside the repo's gitignored `.venv`. Deleting it
was denied twice by the operator permission gate, so instead the repo was extracted at the *same*
revision (`7b11061084e2`) with `git archive` into a clean tree containing zero `.pem` files:
**259 passed, 0 failed**. Health corrected to `pass`.

**`l9-ci-sdk` — a real finding that the sandbox had been masking.** This was recorded as "sandbox
limitation, health undetermined" after zizmor got HTTP 401. That 401 is genuine but environmental:
the sandbox exports a 14-character *sentinel* `GH_TOKEN`, which makes zizmor switch from offline to
online mode, and `github.com` rejects the sentinel at `git-upload-pack` (an unauthenticated
`git ls-remote` to the same URL succeeds). With the invalid credential removed, zizmor runs offline,
the gate proceeds — and finds:

```
.github/workflows/l9-nightly.yml:20   secrets: inherit
  → grants the reusable workflow Quantum-L9/l9-ci-core/.github/workflows/nightly.yml@0d3d8d3
    ALL parent secrets
  → rule secrets-inherit, medium severity, audit confidence High, unsuppressed
13 findings (1 ignored, 11 suppressed): 1 medium
```

It is pre-existing on `main` (file last changed by #89), this session modified no workflow in that
repository, and the repo ships `.github/zizmor.yml` suppressing 11 other findings — this one is not
suppressed, so `make ci` fails on it legitimately.

**Not fixed here.** Narrowing `secrets: inherit` to an explicit list is a security change to a nightly
workflow calling a Core reusable workflow; which secrets it actually needs is a judgement about Core's
contract, and guessing risks breaking the nightly. Recorded for its owner rather than patched
speculatively.

So Layer 1 remains `fail` — but now for one repository, on one finding, for the first time in this
run. The lesson worth keeping: an invalid credential in the environment did not merely block a check,
it **hid a real one**.

## Layer 4 re-run: two corrections and one documented non-goal

Layer 4 was re-aggregated after Layers 2 and 3 completed. Three things changed.

### 1. Cross-layer SHA alignment is now an explicit non-goal

Operator decision, 2026-09-07: repository heads advance as PRs merge, so any single-revision
alignment across layers is stale the moment the next PR lands, and chasing it would mean re-running
earlier layers after every merge for no gain in truth.

`evidence/layer-4/receipts/sha-consistency.json` is therefore `informational` rather than a pass/fail
gate. It carries a `scope_decision` block stating the decision, why it is safe, and what is still
asserted. What the receipt still records, per repository, is the revision each layer used — because
traceability does not depend on alignment: every seam and corridor receipt names the revision it
used, and each artifact is digest-linked to the run that produced it. A **missing** revision would
still be a gap; a **differing** one is not.

Two repositories show more than one revision, both explained in the receipt: `l9-ci-debt-resolver`
(the `resolver_to_intelligence` seam was deliberately re-run at `2410fae` to satisfy the resolver's
own `SnapshotMismatchError` gate, which requires the SDK bundle and the failed CI run to share a
revision) and `l9-ci-core` (the driver, whose branch head advanced as this run committed receipts to
it — `git diff 4c842cb..86acb51 -- .github/actions/ tools/` is empty, so the code that drove the SDK
is byte-identical across all three).

### 2. The published organism receipt was stale, and is corrected

The previously published `04-organism-receipt.json` claimed:

> "Layers 2 and 3 have not run at all, so every seam, every corridor and every fail-closed negative
> test remains unproven ... only one of the three Layer 1 non-zero results is a repository defect
> (l9-ci-core mypy stub declarations)."

Both halves were wrong by the time it was published. Layers 2 and 3 *had* run — their real statuses
(`partial`, `partial`) sat in the same file two keys below that sentence — and the l9-ci-core mypy
claim had already been retracted elsewhere in this very report. `layers_not_executed` listed
`seam_tests` and `corridor_tests` while `layers.seam_tests.status` read `partial`.

The cause was hand-written prose in the generator: every count, name and verdict in the receipt and
the deploy summary was typed in rather than read from the receipts. Those strings were correct when
the first Layer 4 pass ran with only Layer 1 present, and drifted silently as evidence arrived.

The generator now **derives** all of it — `layers_executed`, every failure summary, every table row,
the negative-test tally and the final statement come from the receipts themselves. A comment at each
site records why. Corrected figures:

| | Published (stale) | Corrected |
|---|---|---|
| Layers executed | repo_health only | repo_health, seam_tests, corridor_tests |
| Layer 1 | 7/9 health, 0 repo defects | 8/9 health, 1 repo defect (l9-ci-sdk) |
| Layer 2 | 4/7 pass, 3 partial | 5/7 pass, 2 partial, 0 fail |
| Layer 3 | 3/6 pass, 3 skipped | 4/6 pass, 2 skipped, 0 fail |
| Negative tests | 9/15 passed, 6 not run | 10/15 passed, 5 not run, 0 failed |
| resolver_to_intelligence | partial, "no GitHub credential" | pass, after #52 merged |
| learning_feedback | skipped | pass |

The decision itself does not move: `fail` / `do-not-deploy`, for better-stated reasons.

### 3. Verification

`evidence/layer-4/logs/layer-4-output-digests.json` self-verifies against the copies published here:
15 of 15 outputs match on both `sha256` and `size_bytes`, 0 mismatched, 0 missing.
