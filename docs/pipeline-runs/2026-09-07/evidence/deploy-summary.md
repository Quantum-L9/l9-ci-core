# L9 CI Debt Organism Deploy Summary

Generated: 2026-09-07T13:27:40Z
Final receipt: `artifacts/organism/04-organism-receipt.json`

## Decision

**Do not deploy.**

Status `fail`, decision `do-not-deploy`. Layer 1 has been executed. **Layers 2 and 3 have not been
executed at all**, so no seam, corridor, or fail-closed negative test is proven. Layer 4 aggregates
evidence and is forbidden from creating it, so the gap is reported rather than filled in.

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
| Seam tests | not executed | `artifacts/organism/02-seam-tests.json` (absent) |
| Corridor tests | not executed | `artifacts/organism/03-corridor-tests.json` (absent) |

### Layer 1 detail

| Repo | Version | SHA | Install | Health command | Health |
|---|---|---|---|---|---|
| l9-ci-debt-intelligence | 0.2.0 | `7b11061084e2` | pass | `pytest -q (clean git archive extraction of the same revision)` | pass |
| l9-ci-debt-lsp | 1.0.0 | `ebec362448ef` | pass | `pytest -q` | pass |
| l9-ci-debt-resolver | 0.7.0 | `2c7406c02351` | pass | `pytest -q` | pass |
| l9-ci-core | 2.0.0.dev1 | `4c842cb838b6` | pass | `make check` | pass |
| l9-ci-sdk | 2.0.0 | `cb765cbd4a9c` | pass | `make ci` | fail |
| l9-assurance | 2.1.1 | `e9f012bf42af` | pass | `python scripts/ci.py` | pass |
| l9-harness | 2.0.4 | `25bbb4046ed5` | pass | `pytest -q` | pass |
| l9-pr-repair | 0.4.0 | `f5773d4ded37` | pass | `pytest -q` | pass |
| l9-observability-core | 1.0.0 | `6a84c783f2fb` | pass | `make ci` | pass |

Two non-zero health results, and **neither is a repository defect**:

1. **l9-ci-sdk — sandbox limitation.** `make ci` -> `make hooks` runs zizmor, which fetches
   `github.com/actions/checkout.git` and gets HTTP 401. The test suite was never reached, so this
   repo's health is *undetermined*, not failing.
2. **l9-ci-debt-intelligence — harness contamination.** 258 of 259 tests pass. The one failure is a
   publication-boundary invariant correctly flagging
   `.venv/.../pip/_vendor/certifi/cacert.pem` — a file this Layer 1 harness created by running
   `ensurepip` into the repo's gitignored `.venv` to repair a pip-less venv left by the session-deps
   hook. Removing it was attempted and **denied by the operator permission gate**, so the
   contamination persists in the workspace.

**Retracted finding.** An earlier revision of this run recorded `l9-ci-core` as the sole repository
defect, citing 4 mypy `Library stubs not installed` errors and claiming `types-jsonschema` was
undeclared. That was wrong. `.github/workflows/self-ci.yml` installs `requirements-ci.txt` **and**
`requirements-repo-runtime.txt`, and the latter declares `jsonschema`, `types-jsonschema`, `mypy`
and `ruff`. The harness installed only the first, so mypy resolved from outside the venv and saw
neither stub package. With both installed, `make check` exits 0 — ruff clean, 105 files formatted,
"Success: no issues found in 23 source files". l9-ci-core passes.

## Active seam results

Layer 2 ran. **4 of 7 pass, 3 partial, 0 fail.**

| Seam | Status | Evidence / blocker |
|---|---|---|
| core_to_sdk | pass | Core's own invoke-sdk action drove the SDK; contract identity 2.0.0 verified |
| sdk_to_assurance | pass | Real SDK observation admitted: accepted 1, rejected 0 |
| resolver_to_intelligence | partial | `AuthenticationError` — no GitHub credential for the resolver's transport |
| intelligence_to_lsp | partial | `PublicationGateError` — no promotion-eligible candidates in a 0-finding corpus |
| harness_to_assurance | pass | Real Assurance invoked; `authoritative: false` recorded |
| pr_repair_standalone | partial | `SURFACE_UNSUPPORTED_GRAPHQL` — 403 on live review ingest |
| observability_contracts | pass | Deterministic digest; malformed input rejected |

No active seam failed, and no seam was forced with a hand-authored artifact.

## Corridor results

Layer 3 ran. **3 of 6 pass, 3 skipped, 0 fail.**

| Corridor | Status | Meaning |
|---|---|---|
| ci_evidence | **pass** | Core-driven SDK evidence reaches Assurance, same run, digest-linked |
| assurance_harness | **pass** | Harness invokes Assurance without authority confusion |
| observability_contracts | **pass** | Digest/validation boundary holds |
| learning_feedback | skipped | required seam `resolver_to_intelligence` failed |
| editor_advisory | skipped | required seam `intelligence_to_lsp` partial |
| standalone_repair_safety | skipped | required seam `pr_repair_standalone` partial |

A skipped corridor is not a pass, so Layer 3 is `partial`. None was forced with a hand-authored
artifact.

## Inactive by design

No planned seam was claimed as active — verifiably, because Layers 2 and 3 made no claim at all. The
organism carries **no declaration** of which seams are planned versus live (both declaration files
absent), so this is recorded `unverified`, not `acceptable`.

Noted for Layer 2: `l9-ci-debt-resolver/.l9/pr-repair-delegation-contract.yaml` exists on disk. A
contract file is not proof of a live seam.

The rename did not activate `pr_repair_to_intelligence_learning_packet` or
`resolver_to_pr_repair_delegation`.

## Negative tests

**9 of 15 ran and passed; 6 not run; none failed.** Assurance rejects unknown artifact fields
(`EVIDENCE_SCHEMA_INVALID`), out-of-range SDK versions (`EVIDENCE_PRODUCER_VERSION_REVOKED`) and
tampered digests (`EVIDENCE_PAYLOAD_DIGEST_MISMATCH`). Intelligence is duplicate-safe. Harness refuses
to pass when Assurance is missing and marks itself non-authoritative. pr-repair cannot push by default.
Observability rejects malformed events and holds no control authority. The 6 not-run tests sit behind
a blocked producer, not behind a skipped check.

## Failures

| Code | Detail |
|---|---|
| `LAYER_1_NOT_PASSING` | 0 repo defects. 1 sandbox limitation (l9-ci-sdk), 1 harness artifact (l9-ci-debt-intelligence) |
| `LAYER_2_NOT_EXECUTED` | All 7 active seams unproven |
| `LAYER_3_NOT_EXECUTED` | All 6 live corridors unproven |
| `NEGATIVE_COVERAGE_ABSENT` | All 15 fail-closed negative tests |

## Waivers

None, and none would be admissible: a waiver requires all safety-critical seams to pass, and none
has been run.

## Final statement

The organism is not deploy-ready, and the honest reason is that it is largely untested rather than
proven broken. Layer 1 now shows nine repositories present at their `origin/main` tips, installing
cleanly, with seven passing their own native health command; **neither of the two that did not is a
repository defect** — one is this harness's own contamination, the other a sandbox network
restriction that stopped the suite before it ran. But every question Layer 4
exists to answer — does real SDK output reach Assurance, does resolver feedback reach Intelligence
without leaking, does an Intelligence pack load in the LSP, does Harness stay subordinate, does
l9-pr-repair stay dry-run, does Observability stay out of the control path — belongs to Layers 2 and
3, which have not run. Until they do, `do-not-deploy` is the only defensible verdict.
