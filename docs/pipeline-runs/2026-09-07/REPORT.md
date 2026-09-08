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

## Full re-run of Layers 1–4 against current `origin/main`

Re-run after `l9-ci-debt-resolver#52` merged, so the receipts describe the fleet as it is on
`main` rather than as it was mid-session.

### Layer 1 was rebuilt, not just re-executed

The v2 driver ran **inside the working clones** (symlinked from `/home/user`). Two of this run's
three worst receipt errors trace directly to that:

- Its own `ensurepip` repair of pip-less venvs wrote a pip-vendored `cacert.pem` into
  `l9-ci-debt-intelligence`'s gitignored `.venv`. A sound publication-boundary invariant flagged
  it, and the repository was reported as failing. The failure was the harness's. Deleting the file
  was denied twice by the operator permission gate, so it could not be cleared by cleanup.
- It tested whatever `HEAD` the clone happened to be on. After #52 squash-merged, the clone's
  feature branch and `origin/main` diverged, and the receipt named `2c7406c` — a revision that
  predates the fix it was reporting as passing.

`run_layer1_v3.sh` extracts each repository with `git archive` at its **fetched `origin/main`**
into a fresh tree, re-inits it as a git repo at that exact content (l9-ci-sdk's `make ci` runs
pre-commit over all files and then `git diff`, so it needs one), and installs and tests there. The
working clones are read-only. An extracted tree has no `.venv`, so that class of false failure
cannot recur — and "which revision was tested" has one answer per repository.

`GH_TOKEN`/`GITHUB_TOKEN` are unset for the health step. This sandbox exports a 14-character
sentinel; a non-empty token flips zizmor from offline to online mode, github.com rejects the
sentinel at `git-upload-pack` with 401, and l9-ci-sdk's gate aborts before its audit runs.

### Layer 1 result

**9/9 install, 8/9 health, 1 repository defect.**

| Repo | Revision (`origin/main`) | Version | Health | Result |
|---|---|---|---|---|
| l9-ci-debt-intelligence | `7b11061084e2` | 0.2.0 | `pytest -q` | pass |
| l9-ci-debt-lsp | `ebec362448ef` | 1.0.0 | `pytest -q` | pass |
| l9-ci-debt-resolver | `57cf94e1c8a7` | 0.7.0 | `pytest -q` | pass |
| l9-ci-core | `4c842cb838b6` | 2.0.0.dev1 | `make check` | pass |
| l9-ci-sdk | `cb765cbd4a9c` | 2.0.0 | `make ci` | **fail** |
| l9-assurance | `e9f012bf42af` | 2.1.1 | `python scripts/ci.py` | pass |
| l9-harness | `25bbb4046ed5` | 2.0.4 | `pytest -q` | pass |
| l9-pr-repair | `f5773d4ded37` | 0.4.0 | `pytest -q` | pass |
| l9-observability-core | `6a84c783f2fb` | 1.0.0 | `make ci` | pass |

`l9-ci-debt-intelligence` passes on its own terms in a clean tree — not waived. The environmental
category is now empty: the single failure is a real repository defect, filed as
[Quantum-L9/l9-ci-sdk#96](https://github.com/Quantum-L9/l9-ci-sdk/issues/96) with the full
callee-chain analysis. It is not patched here, because whether `secrets: inherit` is correct for
that caller is a judgement about Core's secret contract.

### Two receipt corrections

**`resolver_to_intelligence` named a revision it did not test.** The receipt read `status: pass`
with `producer_sha: 2c7406c` — a pre-fix commit that could not have produced a pass — while its own
`resolved` block said the merged fix was used with an unmodified CLI. Corrected to `57cf94e`
(`origin/main`), with the proof recorded: `git diff --stat 8a5718e 57cf94e` is empty over the whole
tree, so the code that ran the seam is byte-identical to `main`. The now-fixed defect is retained
as `historical_blocker` — this receipt is the record that found it — rather than presented as live.
The distinct role of `2410fae` (the revision of the *acquired failed CI run*, paired with an SDK
bundle at the same commit to satisfy the resolver's own `SnapshotMismatchError` gate) is now stated
rather than left to be inferred.

**`intelligence_to_lsp` cited a stale corpus size.** "1 record from a real 0-finding SDK bundle" was
true before the resolver feedback event was ingested. The seam receipt records `record_count=2`,
`candidate_count=2`, `promotion_eligible_count=0` — one producer and one scope, so the recurrence
threshold cannot be met by construction. The gate is behaving correctly; the seam is unproven for
lack of corpus maturity, not for lack of a working path.

### Duplicate log tree removed

`evidence/layer-1/logs/` was a byte-for-byte copy of `evidence/logs/`, and it had already drifted —
the two copies of `l9-ci-sdk-health.log` disagreed, one predating the token fix. Duplicated evidence
that can drift is a defect in an evidence bundle. `evidence/logs/` is the path every receipt names,
so it is canonical; the 24-file duplicate is deleted (all were tracked and referenced by nothing).

### Unchanged

Layers 2 and 3 keep their statuses — `partial` and `partial`. Nothing merged that would move
`intelligence_to_lsp` or `pr_repair_standalone`, and re-running passing seams at a new SHA would
only restate them. Cross-layer SHA alignment remains a documented non-goal.

### Verdict

Unchanged: **`fail` / `do-not-deploy`**. The organism now has one honest blocker in Layer 1
(l9-ci-sdk#96), two partial seams, and two corridors skipped behind them.

Digest manifest re-verified against the published copies: **15/15** match on `sha256` and
`size_bytes`, 0 mismatched, 0 missing.

## Closing three gaps in the evidence bundle itself

Audited on request. All three were defects in how this run *recorded* evidence, not in the
organism's results — the verdict is unchanged — but each made a receipt claim more than it had
established.

### 1. The Layer 2 seam inventory was never written

The Layer 2 contract requires `layer-2/seam-inventory.json` **before** the seam tests run, so the
set of seams claimed active is fixed in advance and cannot be chosen to fit the results. That
ordering was not followed.

The file now exists, reconstructed from the seam receipts, `inactive-by-design.json` and the Layer 1
receipt — and it says so in a `provenance` block: written *after* the tests, an accurate index of
what was tested, **not** independent evidence that the active set was declared in advance. Recorded
rather than backdated.

### 2. Receipts declared artifacts by bare filename

Of 45 artifact entries across the 13 seam and corridor receipts, only 10 carried a resolvable
`path`. The rest were `{name, sha256}` — `"resolver-feedback-event.json"` with nothing saying where
it is. The artifacts all existed; the records just could not be followed to them.

Each entry now carries a `path`, resolved **by digest** rather than by name-matching, so the path
points at the file whose content the receipt actually hashed. Where two artifacts are byte-identical
by design — `observability-digest-1.txt` and `-2.txt`, whose equality *is* the determinism proof —
the name disambiguates.

### 3. Seventeen recorded digests no longer matched the published bytes

This one was self-inflicted, and the audit is what surfaced it.

The publishing gate runs `end-of-file-fixer` and `trailing-whitespace` over every committed file,
including this evidence tree. Tracing one artifact through the PR's history shows the same file
alternating between two digests across four publish cycles — `458777…` each time evidence was copied
from the run workspace, `dc6cb9…` each time the gate normalised it. Earlier in this run the Layer 4
output manifest was fixed by recomputing it after normalisation. **The Layer 2 and Layer 3 receipts'
own recorded digests were never revisited**, so they still described pre-normalisation bytes.

Rather than overwrite the record to match the files — which would be making a check pass by
weakening it — every affected entry now carries both, with the delta proven rather than asserted:

| Field | Meaning |
|---|---|
| `sha256_as_emitted` | what the producer wrote |
| `sha256_as_published` | what verifies against the copy in this repository |
| `normalisation` | why they differ, and how equality was established |

Equality was proven per file type: JSON artifacts parsed and compared **as objects**, text artifacts
compared with trailing whitespace stripped. All 17 are whitespace-only. **Zero content differences.**

### The check that should have caught this

Layer 4 Step 9 is supposed to verify "every artifact path referenced by Layer 2 and Layer 3
receipts". It walked the receipts for a bare `path` key — invisible to 35 of 45 entries — then
unioned the result with a glob of the payload tree, so `missing_artifacts` came back empty however
many declarations were unresolvable. It reported `pass` by enumerating files that exist, which is
the opposite of what the step is for. It also never compared a recorded digest against a file, so
the drift above could not have been caught by it.

Step 9 now resolves **every** declared entry and verifies its digest, reported separately from the
tree inventory. And it is falsifiable — which the old check was not. Injecting one wrong digest and
one non-existent path into a corridor receipt makes it fail, naming both:

```
status: fail | unresolved: 1 | mismatch: 1
  caught mismatch:   semgrep-raw.json
  caught unresolved: finding-bundle.json
```

Restored, it returns `pass 45/45`.

### Result

- Declared artifacts: **45 declared, 45 resolved and digest-verified, 0 unresolved, 0 mismatched**
- Layer 4 output manifest: **15/15** match on `sha256` and `size_bytes`

Both verified against the copies published here, not against the run workspace.

Verdict unchanged: **`fail` / `do-not-deploy`**.

## Receipt correction (2026-09-07, post-run)

The published organism JSON and `deploy-summary.md` were corrected without rerunning Layers 2 or 3.
Verdict stays `fail` / `do-not-deploy`. `ci_evidence` stays pass.

1. **L3-F1 carried into Layer 4.** `ci_evidence_transport=pass` is split from
   `assurance_policy_evidence_completeness=open/indeterminate`. Finding
   `ASSURANCE_POLICY_EVIDENCE_INCOMPLETE` (`L3-F1`) is on `04-organism-receipt.json`. The
   seven-control matrix is reconstructed from receipts already on disk; the seventh row is the
   open gap. No determinate Assurance policy/profile is claimed.
2. **VERSION-GOVERNED provenance.** `provenance_model=version-governed`,
   `source_sha_gating=false`. New `layer-4/receipts/version-consistency.json` records producer
   version → emitted contract → consumer version → accepted range → compatibility. SHA
   consistency stays informational. Artifact SHA-256 remains integrity evidence.
3. **Inactive-seam aggregation.** Six planned seams are `planned-or-not-live`.
   `observability_control_plane` stays `prohibited`. The reconstructed-after-run seam-inventory
   warning is preserved.

## Layer 1 refresh after l9-ci-sdk#95 (2026-09-07)

https://github.com/Quantum-L9/l9-ci-sdk/pull/95 merged at `5405fa5768b0` (squash). That commit
removes the nightly Core callers that carried `secrets: inherit`.

SDK native health was re-run only: `make ci` with `GH_TOKEN`/`GITHUB_TOKEN` unset → exit 0
(zizmor Passed, mypy clean, 505 pytest passed). Layer 1 is now **9/9 pass**. `LAYER_1_NOT_PASSING`
is resolved. Layers 2 and 3 were **not** replayed.

Verdict still **`fail` / `do-not-deploy`**: two partial seams, two skipped corridors, L3-F1 open,
five negative tests not run.

## Layer 2 and Layer 3 rerun (2026-09-08)

Targeted replay of the two blocked seams, the five previously not-run negatives, and the two
skipped corridors. Active set declared first in
`evidence/layer-2/seam-inventory.rerun-2026-09-07.json`. Passing seams were not re-forced.

### Errors that were real, and what changed

1. **Five negatives marked `not-run`.** They were never missing implementations. Ran them:
   Intelligence unknown/planned producer quarantine (2 unittest cases), LSP bad protocol + bad
   SDK contract, pr-repair missing `expected_block` and stale `expected_block`. **15/15 pass.**
   `NEGATIVE_COVERAGE_INCOMPLETE` is resolved.
2. **`pr_repair_standalone` / `SURFACE_UNSUPPORTED_GRAPHQL`.** Live `ingest-review` of the real
   Core PR #148 Copilot review event reached `api.github.com/graphql` with a bound token and
   wrote a schema-valid payload (0 actuation findings — Copilot could not review files). Dry-run
   consumed that live payload: no modified files, no push, `protected_paths_touched=false`.
   Seam is **pass**. `standalone_repair_safety` is **pass**, composed on that payload.
3. **Stale “0-finding SDK bundle” text** on `intelligence_to_lsp` / `editor_advisory` / Layer 3
   summaries. The corpus is finding-bearing (`record_count=2`, `candidate_count=2`). Promotion
   remains 0 because recurrence is one producer and one scope. Text corrected.

### What did not move

`intelligence_to_lsp` stays **partial** (`PublicationGateError`). Assemble was re-attempted
against the published compilation; this worktree lacks `pyarrow`, so parquet snapshots could
not be reloaded. On-disk `compilation-result.json` still records `promotion_eligible_count=0`.
No pack was assembled. `editor_advisory` stays **skipped**. No hand-authored pack.

Verdict still **`fail` / `do-not-deploy`**: 6/7 seams pass, 1 partial; 5/6 corridors pass, 1
skipped; L3-F1 open. No waivers.

## Layer 4 identity + aggregation catch-up (2026-09-08)

Focused on the four-layer organism tree (`docs/pipeline-runs/2026-09-07/`), not WIP.

The live repair repository is already `https://github.com/Quantum-L9/l9-pr-repair`. Layer 4
still carried pre-rerun aggregation: Decision said Layer 1 `fail` and `2` partial seams /
`2` skipped corridors; SDK notes said Layers 2 and 3 were not replayed; `pr_repair_standalone`
residue still named a local `PR_Repair` checkout. Those are corrected.

`repository_renames.PR_Repair` stays as the historical GitHub-rename map. It is not a live
identity. Authority-boundary observations that were already proven by passing seams (Core,
SDK, resolver, Intelligence ingest/compile, live `l9-pr-repair` dry-run) are now marked
`verified`. `defense_pack_production` remains unproven.

Verdict unchanged: **`fail` / `do-not-deploy`**. `intelligence_to_lsp` is still partial;
`editor_advisory` is still skipped; L3-F1 is still open.

## Build of core150_l23_blockers_1cd2a947 (2026-09-08)

Executed on `/Users/ib-mac/.l9/worktrees/l9-ci-core-pr150` @ `claude/layer-4-activation-wb6bgf`. WIP and Cursor-Governance remediator files were not touched.

### intelligence_to_lsp — still partial

Shipped `scoring.py` promotes at **4.0**. On-disk candidates score **0.35** (`deferred`). Static findings ceiling is **2.5**. The missing 2.5 is effort + repair_success + false_positive_safety from `l9.historical-resolution-event/v1`.

Historical miner was run against real GitHub `Quantum-L9/l9-ci-core#148` with a bound token. It harvested observations; safety quarantined **59/59** as `sensitive_content` (git SHA / object-id fields). Normalized=0, native events admitted=0, no pack. `editor_advisory` stays skipped. No hand-authored pack.

### L3-F1 — still open

Shipped `evaluate --profile l9.pull-request@1 --policy l9.organization-default@1` on the existing `ci_evidence` envelope exited **42** (`EVIDENCE_SCHEMA_INVALID`: `artifacts[0].sdkVersion` unexpected). Re-admit accepted 0 / rejected 1. No `AssuranceDecision` was written. Fields were not stripped to force a decision. Original organism admission/transport remains pass; that is not a profile decision.

Verdict still **`fail` / `do-not-deploy`**. No waivers.

## L3-F1 closed (2026-09-08T02:30:00Z)

The previous evaluate exit 42 was a **wrong Assurance checkout**: the dependabot worktree omitted `artifact.sdkVersion`. Shipped `origin/main` `e9f012b` already lists the field.

Shipped SDK 2.0.0 @ `5405fa5` then produced the five missing pull-request observations against clean `l9-pr-repair@f5773d4`:

| Check | How | Status |
|---|---|---|
| `l9.mandatory-findings` | existing organism observation | passed |
| `l9.sdk-validation` | `l9-ci observation project-sdk-validation` | passed |
| `l9.repository-metadata` | `l9-ci observation project-repository-metadata` | **failed** (no committed `MANIFEST.md`) |
| `l9.lint` | `ruff check src` exit 0 + `observation build` | passed |
| `l9.tests` | `pytest -q` exit 0 + `observation build` | passed |
| `l9.transport-packet` | `semgrep --config l9-transport.yml --error` exit 0 + `observation build` | passed |

Admit accepted 6 / rejected 0. Evaluate wrote `AssuranceDecision` `dec_d0e45c245bb22fb4417831f5540a852af93e3f09`, verdict **fail**. Completeness is complete. L3-F1 is closed. Remaining organism failures: Layer 2 partial, Layer 3 partial, profile fail. `do-not-deploy` unchanged.

Evidence: `docs/pipeline-runs/2026-09-07/evidence/layer-3/payloads/ci_evidence/profile-evidence/`.

## Layer 3 rerun (2026-09-08T03:05:00Z)

Declared inventory from live Layer 2 seams (`layer-3/corridor-inventory.json`): five corridors ready, `editor_advisory` blocked.

`assemble-defense-pack` on the published compilation still raised `PublicationGateError`.

The acquisition SHA screen was the miner wall: GitHub identity is a 40-character object id, and `inspect_value` treated that as `sensitive_content`, so Core #148 quarantined 59/59 and reconstruction saw no pull request. Fix is Intelligence `3713836` (`feat/historical-acquisition-allows-provider-shas`). Corpus ingress still rejects bare SHAs.

Remine of #148 after the fix: 59 harvested, 2 quarantined, 60 normalized, 1 episode, 3 native events accepted. Batch of 44 more merged PRs across the nine live constellation repos: 69 episodes, 207 events. Corpus snapshot `cs_a153b951…` has **210** records.

Compile `compile_5feb52ee…`: **60** candidates, **0** promotion-eligible, max score **0.95** (`deferred`). Recurrence reached 5/1-scope; `effort`, `repair_success`, and `false_positive_safety` stayed 0.0 because reconstructed outcomes were not `clean_verified` / `target_failure_resolved` and `effort_minutes` is never derived. Threshold 4.0 was not lowered. No pack. No hand-authored pack.

Layer 3 stays **partial** (5 pass / 1 skipped / 0 fail). Layer 4 was **not** entered.

Verdict unchanged: **`fail` / `do-not-deploy`**.
