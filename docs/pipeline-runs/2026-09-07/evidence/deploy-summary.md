# L9 CI Debt Organism Deploy Summary

Generated: 2026-09-07T15:02:54Z
Final receipt: `artifacts/organism/04-organism-receipt.json`

## Decision

**Do not deploy.**

Status `fail`, decision `do-not-deploy`. All four layers ran: Layer 1 `pass`, Layer 2
`partial`, Layer 3 `partial`. Nothing failed outright, but `1`
seam is partial (`intelligence_to_lsp`) and `1` corridor is skipped behind it
(`editor_advisory`), so those paths are unproven rather than proven working. Layer 4
aggregates evidence and is forbidden from creating it, so the gaps are reported rather
than filled in. The repair repository is `https://github.com/Quantum-L9/l9-pr-repair`
(formerly `PR_Repair`). `pr_repair_standalone` and `standalone_repair_safety` are pass.

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
| Repo health | pass | `artifacts/organism/01-repo-health.json` |
| Seam tests | partial | `artifacts/organism/02-seam-tests.json` |
| Corridor tests | partial | `artifacts/organism/03-corridor-tests.json` |

### Layer 1 detail

| Repo | Version | SHA | Install | Health command | Health |
|---|---|---|---|---|---|
| l9-ci-debt-intelligence | 0.2.0 | `7b11061084e2` | pass | `pytest -q` | pass |
| l9-ci-debt-lsp | 1.0.0 | `ebec362448ef` | pass | `pytest -q` | pass |
| l9-ci-debt-resolver | 0.7.0 | `57cf94e1c8a7` | pass | `pytest -q` | pass |
| l9-ci-core | 2.0.0.dev1 | `4c842cb838b6` | pass | `make check` | pass |
| l9-ci-sdk | 2.0.0 | `5405fa5768b0` | pass | `make ci` | pass |
| l9-assurance | 2.1.1 | `e9f012bf42af` | pass | `python scripts/ci.py` | pass |
| l9-harness | 2.0.4 | `25bbb4046ed5` | pass | `pytest -q` | pass |
| l9-pr-repair | 0.4.0 | `f5773d4ded37` | pass | `pytest -q` | pass |
| l9-observability-core | 1.0.0 | `6a84c783f2fb` | pass | `make ci` | pass |

9 of 9 repositories pass their own native health
command. 0 repository defect(s) and 0 environmental result(s).

**l9-ci-sdk — resolved after #95.** The original run failed `make ci` on an unsuppressed
`secrets-inherit` finding in `.github/workflows/l9-nightly.yml`. That file was removed when
https://github.com/Quantum-L9/l9-ci-sdk/pull/95 merged (`5405fa5768b0`). A Layer 1 refresh of
SDK only, with `GH_TOKEN`/`GITHUB_TOKEN` unset, now records `make ci` pass (zizmor clean, mypy
clean, 505 pytest passed). Layers 2 and 3 were then replayed on 2026-09-08.

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

Layer 2 ran, then a targeted rerun on 2026-09-08. **6 of 7 pass, 1 partial, 0 fail.**

| Seam | Status | Evidence / blocker |
|---|---|---|
| core_to_sdk | pass | Core's own invoke-sdk action drove the SDK; contract identity 2.0.0 verified |
| sdk_to_assurance | pass | Real SDK observation admitted: accepted 1, rejected 0 |
| resolver_to_intelligence | pass | Live GitHub acquisition after the cross-host redirect defect was fixed and merged (l9-ci-debt-resolver #52) |
| intelligence_to_lsp | partial | `PublicationGateError` — 2 candidates, 0 promotion-eligible: one producer, one scope, so recurrence maturity is unmet |
| harness_to_assurance | pass | Real Assurance invoked; `authoritative: false` recorded |
| pr_repair_standalone | pass | Live `ingest-review` reached GraphQL and wrote a schema-valid payload for Core PR #148; dry-run used that live payload |
| observability_contracts | pass | Deterministic digest; malformed input rejected |

No active seam failed, and no seam was forced with a hand-authored artifact. The remaining partial
is `intelligence_to_lsp`: the organism's own gate correctly refusing to promote an immature
corpus (one producer, one scope). `pr_repair_standalone` is no longer blocked by GraphQL.

## Corridor results

Layer 3 ran, then a targeted rerun on 2026-09-08. **5 of 6 pass, 1 skipped, 0 fail.**

| Corridor | Status | Meaning |
|---|---|---|
| ci_evidence | pass | Core-driven SDK evidence reaches Assurance, same run, digest-linked |
| learning_feedback | pass | Resolver feedback reaches Intelligence over a live corridor — unblocked once #52 landed |
| assurance_harness | pass | Harness invokes Assurance without authority confusion |
| observability_contracts | pass | Digest/validation boundary holds |
| editor_advisory | skipped | required seam `intelligence_to_lsp` is partial |
| standalone_repair_safety | pass | composed on the live ingest payload; dry-run, no mutation |

A skipped corridor is not a pass, so Layer 3 is `partial`. None was forced with a hand-authored
artifact.

## Inactive by design

The detailed Layer 2 inventory already classifies the six planned seams as planned/not-live and
`observability_control_plane` as prohibited. The top-level organism receipt now matches that
classification: planned seams are `planned-or-not-live`, not `unverified`. Absence of a claim is
not treated as an unverified boundary when the inventory already names them planned.

Noted for Layer 2: `l9-ci-debt-resolver/.l9/pr-repair-delegation-contract.yaml` exists on disk. A
contract file is not proof of a live seam.

The rename did not activate `pr_repair_to_intelligence_learning_packet` or
`resolver_to_pr_repair_delegation`.

Layer-2 `seam-inventory.json` was reconstructed AFTER the seam tests ran. That is a methodological
warning, not a backdate.

## Provenance model

Provenance is **version-governed**. `source_sha_gating` is false. Cross-layer SHA alignment remains
informational (`layer-4/receipts/sha-consistency.json`). Compatibility is recorded in
`layer-4/receipts/version-consistency.json`. Artifact SHA-256 remains exact integrity evidence.

## ci_evidence vs Assurance policy completeness

`ci_evidence` / `ci_evidence_transport` stay **pass**: Core-driven SDK evidence was admitted by
real Assurance code, digest-linked, same run. That is not a determinate Assurance policy/profile
decision. Finding `ASSURANCE_POLICY_EVIDENCE_INCOMPLETE` (`L3-F1`) records
`assurance_policy_evidence_completeness: open/indeterminate`. The seven-control matrix lives on
`04-organism-receipt.json`; the seventh row is the open gap.

## Negative tests

**15 of 15 ran and passed; 0 not run; 0 failed.** Assurance rejects unknown artifact fields
(`EVIDENCE_SCHEMA_INVALID`), out-of-range SDK versions (`EVIDENCE_PRODUCER_VERSION_REVOKED`) and
tampered digests (`EVIDENCE_PAYLOAD_DIGEST_MISMATCH`). Intelligence is duplicate-safe and quarantines
unknown or planned producers. LSP rejects a bad defense-pack protocol and a bad SDK contract version.
Harness refuses to pass when Assurance is missing and marks itself non-authoritative. pr-repair cannot
push by default and rejects a missing or stale `expected_block`. Observability rejects malformed
events and holds no control authority. The five previously not-run tests were executed on the
Layer 2/3 rerun; the implementations already existed at the recorded SHAs.

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
| `LAYER_2_NOT_PASSING` | Layer 2 status 'partial': 6 of 7 active seams PASS (core_to_sdk, harness_to_assurance, observability_contracts, resolver_to_intelligence, sdk_to_assurance, pr_repair_standalone); 1 partial (intelligence_to_lsp); 0 fail. No seam was forced with a hand-authored artifact. |
| `LAYER_3_NOT_PASSING` | Layer 3 status 'partial': 5 of 6 corridors PASS (assurance_harness, ci_evidence, learning_feedback, observability_contracts, standalone_repair_safety); 1 SKIPPED because a required Layer 2 seam is not passing (editor_advisory); 0 fail. A skipped corridor is not a pass, and none was forced with a hand-authored artifact. |
| `ASSURANCE_POLICY_EVIDENCE_INCOMPLETE` (`L3-F1`) | `ci_evidence_transport` is pass. `assurance_policy_evidence_completeness` is open/indeterminate: no determinate Assurance policy/profile decision exists. Do not read `ci_evidence=pass` as a completed Assurance profile. |

## Waivers

None, and none would be admissible: a waiver requires every safety-critical seam to pass, and
1 is partial.

## Final statement

The organism is not deploy-ready. All three input layers have now run, so this is no longer a verdict
about missing evidence — it is a verdict on what the evidence shows.

What is proven: real SDK output reaches Assurance in the same run, digest-linked; resolver feedback
reaches Intelligence over a live corridor; Harness invokes Assurance while recording itself
non-authoritative; Observability holds its digest and validation boundary and no control authority;
and 15 fail-closed negative tests reject what they are supposed to reject. Live pr-repair GraphQL
ingest now works; `standalone_repair_safety` is composed on that live payload.

What is not: `intelligence_to_lsp` cannot promote a defense pack because the corpus has one producer
and one scope, so recurrence maturity is unmet — the gate is behaving correctly and the corridor
behind it (`editor_advisory`) is therefore unproven, not working. The SDK `secrets-inherit`
gate failure is resolved by #95; that does not make the remaining partial seam or skipped corridor a pass.

Nothing failed outright and nothing was forced with a hand-authored artifact. But a partial seam is
not a pass and a skipped corridor is not a pass, so `do-not-deploy` is the only defensible verdict.
