# L9-CORE-ALL-OPEN-PRS-2026-10-16 — operator report

Repository `Quantum-L9/l9-ci-core` · `main` = `d3fc1b3049e742cefc619db28c695bef1dbe217b` (unchanged during campaign) · MERGE=False.
Freeze revalidated at start: same `main`, same six heads, no seventh PR (no scope delta).

## 1. Per-PR outcome

| PR | Branch | Original head | Final head | Published | Required `Analyze (central Core)` | All checks | Threads open | Disposition |
|---|---|---|---|---|---|---|---|---|
| #203 | `claude/repo-execution-v2-adoption` | `fea1ad4` | `92b5116fd9e161bce8e8f9fbed7986c0e2db55f2` | `make pr` ×2 | success | 13/13 success | 0 | **READY_FOR_HUMAN_GRANT** |
| #213 | `feat/toolchain-ssot-core-lane1-prb` | `9b8e7dc` | `9b8e7dc637ebffd47af6ad0b65ee7c8700ed80c9` (no change needed) | — | success | 13/13 success | 0 | **READY_FOR_HUMAN_GRANT** |
| #210 | `dependabot/github_actions/github-actions-minor-patch-2642ef3bde` | `cb39d0a` | `7b77f26ea59565485655e03bd7ecac9c04824a71` | `make pr` | success | 13/13 success | 0 | **READY_FOR_HUMAN_GRANT** (upstream provenance limited, §4) |
| #209 | `dependabot/uv/types-jsonschema-4.26.0.20261006` | `edf4656` | `dbf7f7d35cff0c21d80ef2f0266a22a16f5031ae` | `make pr` | success | 13/13 success | 0 | **READY_FOR_HUMAN_GRANT** |
| #201 | `agent/cursor/issues-core-197` | `78a489d` | remote `78a489d` (red); local fix `d55a04041e5d41e35d81a1eb407e313985f6defa` | **blocked** | failure (old head) | 7 ok / 3 fail | 0 | **REMEDIATED locally · BLOCKED on publication** |
| #208 | `dependabot/github_actions/Quantum-L9/l9-ci-core/dot-github/actions/install-consumer-ci-48514e…` | `2518cb3` | remote `2518cb3` (red); local fix `23b579d317a286c53caf478bec0e57d98c19f298` | **blocked** | failure (old head) | 6 ok / 3 fail | 0 | **REMEDIATED locally · BLOCKED on publication** |

Changed paths per PR (final, vs `main`):
- #203: its original 45 paths (V2 adoption) + merge of `main` + `tests/repo_execution/test_core_self_hosting.py` assertion fix and its `MANIFEST.sha256` entry.
- #213: `.github/workflows/org-ci.yml`, `MANIFEST.sha256`, `docs/consumer-lint-test.md`, `tests/workflows/test_org_ci_toolchain_steps.py`.
- #210: `.github/workflows/publish-sarif.yml` (pin + comment `# v3` → `# v4.38.2`), `.github/workflows/sbom.yml`, `MANIFEST.sha256`.
- #209: `requirements-repo-runtime.txt`, `MANIFEST.sha256`.
- #201 (local): `tests/test_unittest_collectability.py`, `tests/workflows/test_analyze_semgrep_event_class.py`, `tests/workflows/test_python_mypy_contract.py`, `MANIFEST.sha256`.
- #208 (local): `.github/workflows/pr-pipeline.yml` (pin + comment `# main@7148fc7` → `# main@48514e0`), `MANIFEST.sha256`.

All updates were merges of `origin/main` into the branch (no rebase, no force-push, no history rewrite).

## 2. Finding ledger (brief leads F1–F8 + new)

| ID | Verdict | Evidence | Closure |
|---|---|---|---|
| F1 manifest drift on 4 red heads | **CONFIRMED** | Each exact head: `unittest discover` → exactly 1 failure, `test_tracked_manifest_matches_the_worktree` | Regenerated per branch after merging `main`; regenerator proven byte-identical on #213's valid manifest; `verify_checksum_manifest` PASS on every branch. No `L9_MANIFEST_CHECK=0`. Closed on #209/#210 (CI green); closed locally on #201/#208. |
| F2 #201 false-green guard | **CONFIRMED** | Witness: injected module-level test → guard OK, scanning `<parent>/tests` (0 files) vs 68 real modules | `parents[1]`, fail-closed on missing/empty dir, `async def` covered, 2 witness tests; real-tree injection now FAILS the guard. 763 tests OK (+12 previously uncollected on `main`, +3 guard). Local `d55a040`. |
| F3 cross-PR manifest collision / stale bases | **CONFIRMED** | Overlap gate: #208↔#213 and #201↔#203 adjacent-line `MANIFEST.sha256` conflicts; #203↔#213 one hunk (trial merge resolves cleanly, 775 tests OK) | All branches now based on current `main`. Remaining collisions are generated-only and resolve by regeneration at the post-merge refresh (§5). |
| F4 #208 first-party pin | **SURVIVES (valid, no-op)** | `48514e0` ancestor of `main`; `install-consumer-ci/` byte-identical at `2a7354e` and `48514e0`; precedent Dependabot bumps #116, #162 merged | Stale comment corrected; manifest regenerated. Not superseded: legitimate pin refresh, zero behavioral delta. |
| F5 #209/#210 external bumps | #209 **SURVIVES**; #210 **SURVIVES with provenance limit** | #209: PyPI resolves 4.26.0.20261006, not a toolchain-lock key, ruff/format/mypy/unittest pass, mypy consumes the new stub (reveal_type probe). #210: patch bumps (codeql 4.38.0→4.38.2, sbom 0.24.2→0.24.3), inputs unchanged, CI green | #210 version comment corrected. Independent upstream tag→SHA verification NOT done (§4). |
| F6 #203 V2 self-hosting / V1 retention | **SURVIVES** (a–h) | Clean merge with `main`; 766 tests, `make validate`, `make check`, `change-policy`, `agent-check` PASS; pinned bridge `ad2970d` admits Core as V2 end to end; V1 fixture executes; V2 extra/missing/reordered fields → `invalid/contract`; failing phase → `fail/command`; `Makefile` ≡ `tools/l9_repo/facades/make-v1.mk` (same blob); no publication targets | No change required. |
| F7 #213 advisory vs fatal | **SURVIVES** | Pinned `b5acce06` (on `main`) action bytes identical to `main`; 52 action/activation tests incl. missing/corrupt executable = infrastructure failure; no `continue-on-error`; no SDK Biome reference | No change required. |
| F8 Dependabot↔manifest seam | **OWNER DECISION (not built)** | 3 of 4 red heads caused by Dependabot not regenerating `MANIFEST.sha256` | Recommendation only: separate owner-approved change; must not weaken fail-closed check or add a privileged auto-commit writer. |
| CQ-1 code-quality bot: dual `unittest` import (#203) | Not a defect | Established pattern in both mock-using modules | Replied + resolved. |
| CQ-2/3 code-quality bot: imprecise `assertTrue(a<=b)` (#203) | **Fixed** | Discrimination proven (fails on missing leaf, names both sets) | `92b5116`, replied + resolved. |
| A1 V1 admission accepts unknown keys after pin advance (#203) | Documented intent | PR-head `AGENTS.md`, runtime doc and docstring: V1 structural validation retired | Owner residual, no change. |
| A2/A3 dropped `derived_documents` token check / `comparison_ref` protection (#203) | Low, design | No test replacement | Owner residual. |
| B1–B3 lost coverage: Repo.mk script existence, OSError→infrastructure, argv literal preservation (#203) | Low | Deleted tests not replaced | Owner residual (test-corpus expansion out of mandate). |
| C1 stale "V1 delegated unchanged" comment in `run.py` | Low | File outside #203 diff | Owner residual. |
| D1 downstream doc link (`Cursor-Governance` plan → `blob/main/tools/l9_repo/Makefile.template`) | Low | Will 404 after #203 merges | Read-only repo; owner residual. |

Downstream consumers: `Quantum-L9/.github` — no references to deleted paths. `l9-ci-sdk` and external V1 consumer repos — **UNKNOWN** (not accessible).

Review census: zero inline threads on all six at freeze; all bot reviews failed on external quota (Copilot 429, Codex usage limit). Three code-quality threads appeared on #203 during the campaign; all dispositioned and resolved. 0 unresolved threads on every PR now.

## 3. Evidence actually executed

- Exact-head `unittest discover` on all six (before and after repair); `make validate`, `make check`, `change-policy`, `agent-check` on merged #203; self-ci lint sequence (toolchain conformance, ruff check, ruff format, mypy) on #209; governance `make pr` gates (gitleaks, pip-audit, overlap) on #203/#209/#210; `precommit-repo` clean on #201/#208.
- `l9-pr-audit` `build_change_ledger.py` run on all six exact heads with complete corpora (`repository_files_complete: true`). **Not producer-validated:** canonical `audit.json` + `build_audit_bundle.py` semantic validation were not completed (#203 alone: 1,625 obligations, 18,521 closure seeds). This report is a targeted evidence-grounded audit, not a canonical l9-pr-audit bundle.

## 4. Blockers and limits (exact)

1. **#201 / #208 publication.** `make pr` overlap gate blocked on `MANIFEST.sha256` adjacency (generated-only). The gate's digest-keyed STOP-LOOPING failure receipt then refuses the documented `PR_OVERLAP` override on the unchanged tree; its design forbids a third clear path. Direct `git push` is refused by the first-publication gate, and would only pass ADR-0052 file-scope because that probe (GraphQL) fails open on this surface — not used. **Unblock:** operator `L9_LOCAL_PUSH_AUTHORIZED=<reason>`, or the post-predecessor-merge refresh (real tree change clears the receipt). Governance defect to report upstream: the receipt should not key a policy-knob (overlap) failure on tree digest alone.
2. **#210 upstream provenance.** `github/codeql-action` and `anchore/sbom-action` are outside session/mission repository scope; tag→SHA mapping rests on Dependabot's commit links (2892aa5 = `update-v4.38.2` merge) and successful CI resolution of the exact pins.

## 5. Merge plan (requires human grant; none recorded)

Method: **merge commit** (repository convention; preserves every SHA referenced by Core action pins — all such pins already point at `main` merge commits). No squash/rebase, no admin merge, no auto-merge.

| Step | PR | Exact head to grant | Pre-merge proof | Post-merge action |
|---|---|---|---|---|
| 1 | #203 | `92b5116fd9e161bce8e8f9fbed7986c0e2db55f2` | green, clean | Re-check #213 (manifest hunk conflicts with #203) |
| 2 | #213 | refreshed head after merging new `main` + manifest regen | re-run CI on refreshed head | grant applies to the refreshed SHA, not `9b8e7dc` |
| 3 | #201 | refreshed head (publication unblocked by the refresh) | CI green on refreshed head | — |
| 4 | #210 | `7b77f26…` if still textually clean vs new `main` (overlap gate showed disjoint hunks), else refreshed | CI on head | — |
| 5 | #209 | `dbf7f7d…` same rule | CI on head | — |
| 6 | #208 | refreshed head (conflicts with #213's manifest line) | CI green on refreshed head | — |

Ruleset: required check `Analyze (central Core)`; `strict_required_status_checks_policy: false`. Because each PR changes disjoint files, a textually clean merge yields a correct manifest; any refreshed head needs a fresh grant (stale grants refused). Authorization path: an APPROVED review or `authorize merge Quantum-L9/l9-ci-core#<n>@<sha>` on GitHub, or an explicit operator merge instruction → `authorize_merge.py --operator-instruction` → `stack_safe_merge.py`, one PR at a time, re-verifying CI after each merge.

## 6. Update 2026-10-19 (supersedes §1/§4 rows for #201 and #208)

Another agent session published fixes to both blocked PRs on 2026-10-19 (author `Claude`, merges of `origin/main` + repair commits). `main` is unchanged (`d3fc1b3`). This session's local commits `d55a040` (#201) and `23b579d` (#208) are **superseded and will not be pushed**.

| PR | New head | Tree vs this campaign's local repair | CI | Threads | Disposition |
|---|---|---|---|---|---|
| #208 | `96e2b9f11951210820cf5a671c30d6e5127bf25c` | byte-identical (empty diff) | 13/13 success | 0 | **READY_FOR_HUMAN_GRANT** (still conflicts with #213 on one `MANIFEST.sha256` line; refresh after #213 merges) |
| #201 | `6cc4f76cc537e91d01623ac8e6f8aacf5948a9d0` | different implementation, superset: fails closed on missing or empty corpus, reports sync+async module-level tests, adds a TestCase-method negative witness | all required checks success; `copilot-pull-request-reviewer` in progress | 0 | **READY_FOR_HUMAN_GRANT** pending Copilot review disposition |

F2 re-verified on `6cc4f76` independently: 6 guard tests pass; an injected `async def test_*` makes the guard fail; `verify_checksum_manifest` PASS. The §4.1 publication blocker no longer applies. All six PRs are now green on their exact heads; merge plan in §5 is unchanged except that #201 and #208 grant their new heads (after the predecessor refresh where §5 requires one).

## 7. Update 2026-10-20 — #210 superseded; scope delta #214

- **#210 CLOSED by Dependabot** (2026-10-20 09:38 UTC: "updatable in another way"), not merged. Disposition: **SUPERSEDED**. Drop it from the §5 merge plan.
- **#214 (new, outside the frozen six)**: `dependabot/...github-actions-minor-patch`, head `9fc6a9e`, base `d3fc1b3` (current). Three pin bumps: codeql-action/upload-sarif 4.38.0→4.38.3 (`24c5418`), anchore/sbom-action 0.24.2→0.24.3 (`66cbf4b`, same SHA #210 had), ossf/scorecard-action 2.4.4→2.5.0 (`fa7117d`, a minor bump). Read-only findings: it does not regenerate `MANIFEST.sha256` for `publish-sarif.yml`, `sbom.yml` or `scorecard.yml` (expected to fail like F1), and it keeps the stale `# v3` comment on upload-sarif. **Not remediated — needs explicit operator scope decision** (and single-writer coordination with the other session active on these branches).
- **Operator decision (2026-10-20):** #214 is left to the other active session. This session now watches only #203, #209 and #213; #201, #208 and #214 are owned by the other session (single writer per branch).
- **Correction to §6 (#201):** after the 2026-10-19 Copilot review (medium: unused `_is_testcase_base` helper), the helper was removed (`b2a3a9a`, other session; content-identical `cb851ef` merged by this session's governed push). #201's current head is `66b3c4a5679862e528c22ab584c447d0c78a35c2`: 13/13 checks success, 0 unresolved threads. Grant that head, not `6cc4f76`.
