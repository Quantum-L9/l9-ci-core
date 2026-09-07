# L9 CI Debt Organism Deploy Summary

Generated: 2026-09-07T14:49:33Z
Final receipt: `artifacts/organism/04-organism-receipt.json`

## Decision

**Do not deploy.**

Status `fail`, decision `do-not-deploy`. All three input layers ran: Layer 1 `fail`, Layer 2
`partial`, Layer 3 `partial`. Nothing failed outright, but `2`
seam(s) are partial and `2` corridor(s) are skipped behind them, so
those paths are unproven rather than proven working. Layer 4 aggregates evidence and is forbidden
from creating it, so the gaps are reported rather than filled in.

## Tested scope

Nine repos in scope, all nine present and recorded by Layer 1:
- l9-ci-debt-intelligence
- l9-ci-debt-lsp
- l9-ci-debt-resolver
- l9-ci-core
- l9-ci-sdk
- l9-assurance
- l9-harness
- l9-pr-repair
- l9-observability-core

Canonical repair repository: https://github.com/Quantum-L9/l9-pr-repair
Legacy name: PR_Repair -> l9-pr-repair
Out of scope: l9-constellation-topology

## Repository identity reconciliation

- Historical `PR_Repair` evidence observed: **no**. Layer 1 recorded the repository as
  `l9-pr-repair` directly, so no normalization was applied and none was needed.
- Canonical SHA: `f5773d4ded37dbb611100166947aa2f78e5fbccc` — and that commit is itself the rename stamp (#63).
- Canonical remote: `https://github.com/Quantum-L9/l9-pr-repair` (matches canonical: true).
- Continuity proven: **yes**, on Layer 1 evidence. Reconciliation status `pass`.

## Layer results

| Layer | Status | Receipt |
|---|---|---|
| Repo health | fail | `artifacts/organism/01-repo-health.json` |
| Seam tests | partial | `artifacts/organism/02-seam-tests.json` |
| Corridor tests | partial | `artifacts/organism/03-corridor-tests.json` |

### Layer 1 detail

| Repo | Version | SHA | Install | Health command | Health |
|---|---|---|---|---|---|
| l9-ci-debt-intelligence | 0.2.0 | `7b11061084e2` | pass | `pytest -q` | pass |
| l9-ci-debt-lsp | 1.0.0 | `ebec362448ef` | pass | `pytest -q` | pass |
| l9-ci-debt-resolver | 0.7.0 | `57cf94e1c8a7` | pass | `pytest -q` | pass |
| l9-ci-core | 2.0.0.dev1 | `4c842cb838b6` | pass | `make check` | pass |
| l9-ci-sdk | 2.0.0 | `cb765cbd4a9c` | pass | `make ci` | fail |
| l9-assurance | 2.1.1 | `e9f012bf42af` | pass | `python scripts/ci.py` | pass |
| l9-harness | 2.0.4 | `25bbb4046ed5` | pass | `pytest -q` | pass |
| l9-pr-repair | 0.4.0 | `f5773d4ded37` | pass | `pytest -q` | pass |
| l9-observability-core | 1.0.0 | `6a84c783f2fb` | pass | `make ci` | pass |

8 of 9 repositories pass their own native health
command. 1 repository defect(s) and 0 environmental result(s):

**l9-ci-sdk — a real repository defect.** `make ci` -> `make hooks` runs zizmor, which reports
`secrets-inherit` (medium, High confidence) at `.github/workflows/l9-nightly.yml:20`: `secrets:
inherit` hands a reusable workflow every parent secret. It is pre-existing on `main`, it is **not**
in the repo's own `.github/zizmor.yml` suppression list, and the repo's own gate fails on it.

This was first recorded as a sandbox limitation, and that diagnosis was wrong in an instructive way.
The sandbox exports a 14-character sentinel `GH_TOKEN`, which flips zizmor from offline to online
mode; github.com rejects the sentinel at `git-upload-pack` with 401, and the gate aborted before the
audit. Unsetting the token lets zizmor run offline — and it then finds this. **The broken credential
was hiding a genuine finding, not creating a false one.**

Not fixed here: whether `secrets: inherit` is correct for this caller is a judgement about Core's
secret contract, and this run's mandate is verification, not repair. Recorded for its owner.

**Method note — why there is no longer an "environmental" column.** Layer 1 v3 tests a clean
`git archive` extraction of each repository's fetched `origin/main`, re-inited as a git repo at
that exact content, in its own seeded venv. The working clones are read-only.

That change retired the one non-defect this run had been carrying.
`l9-ci-debt-intelligence` had reported a failure: a sound publication-boundary invariant flagging
`.venv/.../pip/_vendor/certifi/cacert.pem` — a file the *earlier, in-place* Layer 1 harness created
by running `ensurepip` into the repo's gitignored `.venv` to repair a pip-less venv left by the
session-deps hook. Deleting it was denied twice by the operator permission gate, so the failure
could not be cleared by cleanup. An extracted tree has no `.venv` at all, so the invariant passes
on its own terms rather than being waived. The contamination still sits in the workspace checkouts
and is recorded in the Layer 2 receipt's `workspace_residue`; it no longer touches any result.

The same change makes "which revision was tested" unambiguous, which mattered here:
`l9-ci-debt-resolver` is now tested at `57cf94e` — `origin/main`, carrying the merged redirect fix
from #52 — where the previous run had tested a working-clone branch tip.

**Retracted finding.** An earlier revision of this run recorded `l9-ci-core` as the sole repository
defect, citing 4 mypy `Library stubs not installed` errors and claiming `types-jsonschema` was
undeclared. That was wrong. `.github/workflows/self-ci.yml` installs `requirements-ci.txt` **and**
`requirements-repo-runtime.txt`, and the latter declares `jsonschema`, `types-jsonschema`, `mypy`
and `ruff`. The harness installed only the first, so mypy resolved from outside the venv and saw
neither stub package. With both installed, `make check` exits 0 — ruff clean, 105 files formatted,
"Success: no issues found in 23 source files". l9-ci-core passes.

## Active seam results

Layer 2 ran. **5 of 7 pass, 2 partial, 0 fail.**

| Seam | Status | Evidence / blocker |
|---|---|---|
| core_to_sdk | pass | Core's own invoke-sdk action drove the SDK; contract identity 2.0.0 verified |
| sdk_to_assurance | pass | Real SDK observation admitted: accepted 1, rejected 0 |
| resolver_to_intelligence | pass | Live GitHub acquisition after the cross-host redirect defect was fixed and merged (l9-ci-debt-resolver #52) |
| intelligence_to_lsp | partial | `PublicationGateError` — 2 candidates, 0 promotion-eligible: one producer, one scope, so recurrence maturity is unmet |
| harness_to_assurance | pass | Real Assurance invoked; `authoritative: false` recorded |
| pr_repair_standalone | partial | `SURFACE_UNSUPPORTED_GRAPHQL` — this sandbox 403s GraphQL, so live review ingest is unreachable here |
| observability_contracts | pass | Deterministic digest; malformed input rejected |

No active seam failed, and no seam was forced with a hand-authored artifact. The two partials differ
in kind: `intelligence_to_lsp` is the organism's own gate correctly refusing to promote an immature
corpus, while `pr_repair_standalone` is blocked by this sandbox's transport, not by the code.

## Corridor results

Layer 3 ran. **4 of 6 pass, 2 skipped, 0 fail.**

| Corridor | Status | Meaning |
|---|---|---|
| ci_evidence | pass | Core-driven SDK evidence reaches Assurance, same run, digest-linked |
| learning_feedback | pass | Resolver feedback reaches Intelligence over a live corridor — unblocked once #52 landed |
| assurance_harness | pass | Harness invokes Assurance without authority confusion |
| observability_contracts | pass | Digest/validation boundary holds |
| editor_advisory | skipped | required seam `intelligence_to_lsp` is partial |
| standalone_repair_safety | skipped | required seam `pr_repair_standalone` is partial |

A skipped corridor is not a pass, so Layer 3 is `partial`. None was forced with a hand-authored
artifact.

## Inactive by design

No planned seam was claimed as active by Layer 2 or Layer 3. But the organism carries **no
declaration** of which seams are planned versus live (both declaration files absent), so absence of
a claim is not the same as a verified boundary: this is recorded `unverified`, not `acceptable`.

Noted for Layer 2: `l9-ci-debt-resolver/.l9/pr-repair-delegation-contract.yaml` exists on disk. A
contract file is not proof of a live seam.

The rename did not activate `pr_repair_to_intelligence_learning_packet` or
`resolver_to_pr_repair_delegation`.

## Negative tests

**10 of 15 ran and passed; 5 not run; 0 failed.** Assurance rejects unknown artifact fields
(`EVIDENCE_SCHEMA_INVALID`), out-of-range SDK versions (`EVIDENCE_PRODUCER_VERSION_REVOKED`) and
tampered digests (`EVIDENCE_PAYLOAD_DIGEST_MISMATCH`). Intelligence is duplicate-safe. Harness refuses
to pass when Assurance is missing and marks itself non-authoritative. pr-repair cannot push by default.
Observability rejects malformed events and holds no control authority. The 5 not-run tests sit behind
a blocked producer, not behind a skipped check: intelligence_quarantines_unknown_or_planned_producer, lsp_rejects_bad_defense_pack_protocol, lsp_rejects_bad_sdk_contract_version, pr_repair_rejects_missing_expected_block, pr_repair_rejects_stale_expected_block.

## Cross-layer SHA alignment — a documented non-goal

Layer 4 records the revision each layer used, and does **not** require them to match. Repository
heads advance as PRs merge, so any single-revision alignment is stale the moment the next PR lands;
chasing it would mean re-running earlier layers after every merge for no gain in truth. Traceability
does not depend on it — every seam and corridor receipt names the revision it used and each artifact
is digest-linked to the run that produced it. What is still asserted is that each layer *recorded* a
revision; a missing one would be a gap, a differing one is not. Two repositories show more than one
revision, both explained in `layer-4/receipts/sha-consistency.json`: `l9-ci-debt-resolver` (a seam
deliberately re-run at the revision its own `SnapshotMismatchError` gate demands) and `l9-ci-core`
(the driver, whose branch head advanced as this run committed receipts to it — `git diff` over
`.github/actions/` and `tools/` across all three revisions is empty). That receipt is `informational`.

## Failures

| Code | Detail |
|---|---|
| `LAYER_1_NOT_PASSING` | Layer 1 status is 'fail': 9/9 install, 8/9 pass their own native health command. Repository defects: 1 (l9-ci-sdk). Environmental or harness-caused non-zero results: 0. An earlier revision of this run wrongly recorded l9-ci-core as a repository defect; that finding is retracted and l9-ci-core passes. See the Layer 1 receipt's failures[] for the per-repo diagnosis and attribution. |
| `LAYER_2_NOT_PASSING` | Layer 2 status 'partial': 5 of 7 active seams PASS (core_to_sdk, harness_to_assurance, observability_contracts, resolver_to_intelligence, sdk_to_assurance); 2 partial (intelligence_to_lsp, pr_repair_standalone); 0 fail (none). No seam was forced with a hand-authored artifact. |
| `LAYER_3_NOT_PASSING` | Layer 3 status 'partial': 4 of 6 corridors PASS (assurance_harness, ci_evidence, learning_feedback, observability_contracts); 2 SKIPPED because a required Layer 2 seam is not passing (editor_advisory, standalone_repair_safety); 0 fail (none). A skipped corridor is not a pass, and none was forced with a hand-authored artifact. |
| `NEGATIVE_COVERAGE_INCOMPLETE` | 10 of 15 fail-closed negative tests ran and passed; 5 were not run because their producer is blocked (intelligence_quarantines_unknown_or_planned_producer, lsp_rejects_bad_defense_pack_protocol, lsp_rejects_bad_sdk_contract_version, pr_repair_rejects_missing_expected_block, pr_repair_rejects_stale_expected_block); 0 failed. |

## Waivers

None, and none would be admissible: a waiver requires every safety-critical seam to pass, and
2 are partial.

## Final statement

The organism is not deploy-ready. All three input layers have now run, so this is no longer a verdict
about missing evidence — it is a verdict on what the evidence shows.

What is proven: real SDK output reaches Assurance in the same run, digest-linked; resolver feedback
reaches Intelligence over a live corridor; Harness invokes Assurance while recording itself
non-authoritative; Observability holds its digest and validation boundary and no control authority;
and 10 fail-closed negative tests reject what they are supposed to reject.

What is not: `intelligence_to_lsp` cannot promote a defense pack because the corpus has one producer
and one scope, so recurrence maturity is unmet — the gate is behaving correctly and the corridor
behind it (`editor_advisory`) is therefore unproven, not working. `pr_repair_standalone` is blocked
by this sandbox's GraphQL 403, taking `standalone_repair_safety` with it. And l9-ci-sdk's own gate
fails on an unsuppressed `secrets-inherit` finding that a broken sandbox credential had been hiding.

Nothing failed outright and nothing was forced with a hand-authored artifact. But a partial seam is
not a pass and a skipped corridor is not a pass, so `do-not-deploy` is the only defensible verdict.
