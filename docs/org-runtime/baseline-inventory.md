# L9 CI Core Organization Runtime — Baseline Inventory

Campaign: `l9-ci-core-org-runtime-v1` (TASK-001 evidence)
Bound revision: `cd793fc5fbc480e8057fc360af6287edd191172f` (origin/main at
execution start, verified after fetch).

## 1. Repository identity

- Role (per `.l9/architecture.yaml`): `thin-control-plane` — GitHub Actions
  orchestration and immutable SDK provisioning for the Quantum-L9 CI platform.
- Generation 2, phase 4 (all four clean-room phases implemented).
- SDK: `Quantum-L9/l9-ci-sdk`, pinned `7d7762eae5e1a12fdc66276975e2949891762a20`
  (`.l9/sdk-compatibility.yaml` default; two retained rollback revisions).
- Dependency direction: `l9-ci-core -> l9-ci-sdk` only.

## 2. Current consumer path (what the org runtime must replace)

Today a normal Quantum-L9 repo receives CI by COPYING, in order:

1. Six governance files (`docs/templates/governance/*.yaml`) into the
   consumer's `.github/governance/`.
2. `docs/templates/l9-analysis.yml` into `.github/workflows/` with a
   hard-coded full Core SHA duplicated on the `uses:` line and in `L9_CORE_REF`.
3. Optionally `l9-lint-test.yml` / `l9-lint-test-node.yml` hygiene templates.

Consumer-owned Core revision selection, copied governance pack, copied caller
workflow — all three are exactly the surfaces the campaign objective removes.

## 3. Core execution surface at the bound SHA

- 20 workflows under `.github/workflows/` (inventory enforced by
  `tests/workflows/test_phase_scope.py`): 4 self-CI/contract gates, 2 self-only
  dogfood callers, 5 reusable analysis/publication kernels, 1 nested helper,
  7 v1 compatibility kernels, 1 maintenance workflow.
- 10 composite actions under `.github/actions/`: `resolve-governance`,
  `provision-sdk`, `invoke-sdk`, `validate-bundle`, `route-artifacts`,
  `build-artifact-manifest`, `render-publication`, `publish-check`,
  `validate-governance`, `validate-release`.
- Preferred consumer kernel: `analyze-semgrep.yml` (SDK `semgrep run` →
  bundle validate → `gate evaluate` → agent-payload + SARIF → route →
  manifest → publish).
- Repository execution runtime: `.l9/repo-workflow.json` (artifact version
  4.3.1, authoritative) with `make` delegation to `tools.l9_repo/`
  (`validate`, `change-policy`, `agent-check`, `push`, `pr`, …). Targeted
  change gates: workflows → `tests/workflows`, actions → `tests/architecture`,
  sdk-compatibility → `tests/provisioning`.

## 4. Organization-administration leakage inventory (to remove / re-bound)

| Surface | Path | What it does | Target state |
|---|---|---|---|
| Org ruleset mutation | `tools/apply_org_ruleset.py`, `docs/governance/org-ruleset/l9-required-checks.ruleset.json` | Creates/updates the live Quantum-L9 required-status-checks ruleset via GitHub API; previews pass/fail across every scoped repo | Control-plane authority; not Core |
| Org registry sync | `tools/regenerate_identity_maps.py`, `.github/workflows/regenerate-identity-maps.yml` | Keeps preset semgrep identity maps in sync with the live registry | Control-plane authority; not Core |
| Rollout policy ownership | `.l9/architecture.yaml` `owned_by_core: organization rollout policy` | Claims rollout authority in the ownership contract | Re-bound to control plane |
| Consumer copy path | `docs/templates/` (+ org pack mirror in Quantum-L9/.github — out of scope) | Copy-first integration | Demote to legacy; single org-facing entrypoint |

## 5. Validation baseline (honest, at the bound SHA)

- `python3 -m unittest discover tests`: **FAILED (errors=14)** — 180 tests;
  the 14 errors are a test-fixture defect: `make_git_fixture`
  (`tests/tools/test_l9_repo.py`) copies the repo tree into the temp fixture
  including `.git`, which inside a git worktree is a gitfile pointing at the
  outer repository, so temp-repo git operations hit the real worktree. Fixed
  during this campaign (fixture must exclude `.git`).
- `make validate`: **FAIL** — `MANIFEST.sha256` stale at the bound SHA for
  `AGENTS.md` and three `tests/provisioning/*` files (incoming commits
  `7fa8b63`, `cd793fc` did not regenerate the manifest).
- Both failures are recorded here as baseline; resolved by later tasks
  (TASK-005 validation safety, TASK-008 entropy reduction).

## 6. Ownership reconstruction (source-of-truth)

- Core owns: orchestration, security/trust boundary, immutable SDK
  provisioning, validation/routing, artifact routing, verdict publication,
  the public CI runtime interface.
- SDK owns: provider execution/SPI, provider-native parsing, canonical
  evidence/findings/classification, schema/semantic validation,
  deterministic serialization, agent-payload projection.
- Control plane owns (post-campaign): targeting, Core-version selection,
  rulesets, reconciliation, rollout, rollback, fleet visibility.
- `prohibited_in_core` (`.l9/ownership.yaml`) is unchanged and binding.
