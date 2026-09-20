# v1 Compatibility Layer

## Why this exists

The v2 rewrite of `l9-ci-core` (commit `54a2f2f`, "Overwrite main with v2 rewrite: thin control-plane architecture") deleted the eight reusable "kernel" workflows that the `Quantum-L9/.github` org-starter templates call via `@v1`. Because the repository never carried a `v1` ref, every consumer repository that adopted the starters — `l9-pr-pipeline.yml`, `l9-security.yml`, `l9-scorecard.yml`, `l9-sbom.yml`, `l9-nightly.yml`, `l9-pre-commit.yml`, `l9-governance.yml`, `l9-release.yml`, and the `security`/`scorecard` jobs of `l9-node-ts-monorepo.yml` — failed at workflow startup on every push and pull request with an unresolvable `workflow_call` reference.

This layer restores the eight kernel workflows under their original names, and the repository now carries a `v1` tag so the starter references resolve again.

## What the compat kernels are (and are not)

The kernels are **contract-superset, language-aware shims**. Each one declares every input that the original v0.1.0 kernel declared **plus** every input that the org-starter callers actually pass (`src-directory`, `run-extended-tests`, `run-security`, `working-directory`, `publish-to-pypi`, `run-npm-audit`). This matters because GitHub Actions hard-fails a `workflow_call` when the caller passes an undeclared input — so simply re-tagging the v0.1.0 kernels would have left six of the nine starter callers broken.

| Kernel | Behavior |
|---|---|
| `pr-pipeline.yml` | Detects Python vs Node; runs ruff/mypy/pytest or ESLint/tsc/Vitest-Jest (preferring the repo's own `lint`/`typecheck`/`test` scripts). |
| `security.yml` | Gitleaks secret scan (pinned CLI binary with checksum verification), then pip-audit + bandit for Python and `npm audit` for Node. |
| `scorecard.yml` | OpenSSF Scorecard with results as a build artifact; self-skips on `pull_request` (unsupported by scorecard-action). |
| `sbom.yml` | Syft SPDX-JSON SBOM uploaded as a build artifact. |
| `nightly.yml` | **Superseded v1 shim.** Org nightly kernel: nests `analyze-semgrep.yml` at `profile: nightly` (`ci_deep`, advisory) plus language-aware full-tree tests and informational dependency-freshness reports. Filename unchanged so callers bump the pin. |
| `pre-commit-ci.yml` | Runs `pre-commit run --all-files` when `.pre-commit-config.yaml` exists; notices and passes otherwise. |
| `trio-governance.yml` | Structural three-tier separation check (model must not import service/interface; service must not import interface) across Python and TypeScript sources. |
| `release-publish.yml` | Python: `python -m build` + `twine check` + dist artifact. Node: `npm publish --dry-run`. No unattended registry publication (see below). |

Two deliberate deviations from the v0.1.0 behavior follow from Core's v2 invariants, which are enforced by self-CI on every push:

1. **Least-privilege write.** Core forbids workflow-level write on these kernels (`tests/workflows/test_workflow_permissions.py`). Scorecard therefore does not publish to the code-scanning feed (`publish_results: false`, artifact output instead), and `release-publish.yml` validates and stages artifacts rather than publishing to PyPI/npm. Unattended publication belongs in a repo-owned workflow using trusted publishing. `nightly.yml` is the audited exception: its analyze job grants `checks: write` so the nested `analyze-semgrep.yml` publication can emit a GitHub check. Findings stay advisory on the nightly profile and are not a required merge check.
2. **Third-party actions are SHA-pinned; Core self-refs use `@v1`.** Nested Core workflows and the CORE_ACTIONS_PIN composite set pin to Core's moving major `v1`. Third-party actions stay on full 40-character commit SHAs (`tests/architecture/test_external_action_pins.py`). The gitleaks CLI is a version-pinned, checksum-verified binary rather than the gitleaks-action (which requires a license key on organization repositories). `v2` remains `install-consumer-ci@v2` only.

The kernels never fail because a language toolchain is absent: each gate runs only when it applies to the repository, and inapplicable gates emit a `::notice` and pass. PR/push Semgrep stays on `analyze-semgrep.yml` via the v2 presets (`presets/*/.github/workflows/l9-analysis.yml`). Nightly deep analysis (`ci_deep`, advisory) is no longer a second product: it is the `nightly.yml` kernel at `profile: nightly`.

## Tag policy

`v1` is a **moving compatibility tag**: it points at the newest main-branch commit that preserves the eight kernel contracts, and it may be advanced (never deleted) when the kernels receive backward-compatible fixes. Immutable point-in-time tags (`v1.0.0`, `v1.0.1`, ...) accompany each advancement. Callers that require bit-for-bit stability should pin the kernel by commit SHA, exactly as the v2 preset does for composite actions.

L9-ORG-008 treats `@v1` as an `approved_signed_release_tag` only when GitHub restricts who may move it (tag ruleset or equivalent protection: no deletion, signed updates from the authorized release writer). As of this change the repository has branch rulesets only; classic tag protection and a tag-target ruleset are not set. That is a HUMAN/ops leftover — do not weaken Core self-refs to `@main` to work around it.

## Who may move `v1`

`tools/publish_consumer_ci_tag.sh` is the single authorized writer for every moving compatibility tag (`.l9/release-plane.yaml` → `release_writers`), validated by `tools/check_release_writers.py` and asserted by `tests/release/test_release_writers.py`. It owns `v1` and `v2` because both are the same act — force-move a mutable pointer onto reviewed mainline code. `docs/release/tag-and-release.sh` owns the immutable `vMAJOR.MINOR.PATCH` namespace and never moves an alias; neither writer may reach into the other's namespace.

Adding a second script that moves `v1` is a contract violation, not a convenience.

## Advancing `v1` (bootstrap and promotion)

The writer refuses two classes of bad target before it creates any tag:

1. **A target that is not an ancestor of `origin/main`.** A moving compatibility tag points at reviewed mainline code. Advancing it to an unmerged feature head would let a pull request grant itself the Core revision it is still asking to be reviewed. *Making a pull request green is not a reason to move the tag; merging it is.*
2. **A target missing any Core self-reference action.** Core workflows resolve these seven composite actions through the tag, so a target lacking one fails Organization CI at setup with an unresolvable action reference:

   `resolve-consumer-metadata`, `resolve-governance`, `provision-sdk`, `invoke-sdk`, `validate-bundle`, `route-artifacts`, `build-artifact-manifest`

   This is not hypothetical. The `v1` ref inherited from the pre-rewrite history carried six of the seven; the one it lacked, `resolve-consumer-metadata`, is exactly what Organization CI reported as unresolvable. A commit being on `main` does not by itself make it a compatible `v1` target.

```bash
git fetch origin main
# Target must be a merged main commit. Record the previous SHA in the PR body.
tools/publish_consumer_ci_tag.sh v1 <main-commit-sha>
git push origin v1 --force      # HUMAN: requires tag-write authority
git ls-remote --tags origin v1  # verify the ref resolves to the intended commit
```

The local tag move is repository tooling; **pushing it is a control-plane act**. Tag protection and who holds tag-write authority are GitHub settings outside this repository, so the final two steps are operator work that no check here can perform or attest. Until the remote `v1` actually resolves to a compatible commit, Core workflows pinned to `@v1` will fail at setup — that is a tag-lifecycle state, not evidence against moving tags.

## Consumer guidance

Existing org-starter callers (`uses: Quantum-L9/l9-ci-core/.github/workflows/<kernel>.yml@v1`) now resolve without modification. New consumers should prefer the v2 presets under `presets/` for governed analysis and treat the v1 kernels as baseline hygiene gates. If a kernel input you pass is rejected, you are pinned to a pre-compat ref; move to `@v1` or a post-compat SHA.
