# v1 Compatibility Layer

## Why this exists

The v2 rewrite of `l9-ci-core` (commit `54a2f2f`, "Overwrite main with v2 rewrite: thin control-plane architecture") deleted the eight reusable "kernel" workflows that the `Quantum-L9/.github` org-starter templates call via `@v1`. Because the repository never carried a `v1` ref, every consumer repository that adopted the starters — `l9-pr-pipeline.yml`, `l9-security.yml`, `l9-scorecard.yml`, `l9-sbom.yml`, `l9-nightly.yml`, `l9-pre-commit.yml`, `l9-governance.yml`, `l9-release.yml`, and the `security`/`scorecard` jobs of `l9-node-ts-monorepo.yml` — failed at workflow startup on every push and pull request with an unresolvable `workflow_call` reference.

This layer restores the eight kernel workflows under their original names, and the repository now carries a `v1` tag so the starter references resolve again.

## What the compat kernels are (and are not)

The kernels are **contract-superset, language-aware shims**. Each one declares every input that the original v0.1.0 kernel declared **plus** every input that the org-starter callers actually pass (`src-directory`, `run-extended-tests`, `run-security`, `working-directory`, `publish-to-pypi`, `run-npm-audit`). This matters because GitHub Actions hard-fails a `workflow_call` when the caller passes an undeclared input — so simply re-tagging the v0.1.0 kernels would have left six of the nine starter callers broken.

| Kernel | Behavior |
|---|---|
| `pr-pipeline.yml` | Detects Python vs Node; runs ruff/mypy/pytest or ESLint/tsc/Vitest-Jest (preferring the repo's own `lint`/`typecheck`/`test` scripts). `run-security: true` additionally runs the security compatibility workflow; `false` skips it. |
| `security.yml` | Gitleaks secret scan (pinned CLI binary with checksum verification), then pip-audit + bandit for Python. For Node, `run-npm-audit: true` requires `package-lock.json` and runs `npm audit`; `false` skips npm audit even when the lockfile exists. |
| `scorecard.yml` | OpenSSF Scorecard with results as a build artifact; self-skips on `pull_request` (unsupported by scorecard-action). |
| `sbom.yml` | Syft SPDX-JSON SBOM uploaded as a build artifact. |
| `nightly.yml` | **Superseded v1 shim.** Org nightly kernel: nests `analyze-semgrep.yml` at `profile: nightly` (`ci_deep`, advisory) plus language-aware full-tree tests and informational dependency-freshness reports. Filename unchanged so callers bump the pin. |
| `pre-commit-ci.yml` | Runs `pre-commit run --all-files` when `.pre-commit-config.yaml` exists; notices and passes otherwise. |
| `trio-governance.yml` | Structural three-tier separation check (model must not import service/interface; service must not import interface) across Python and TypeScript sources. |
| `release-publish.yml` | Python: `python -m build` + `twine check` + dist artifact. Node: `npm publish --dry-run`. No unattended registry publication (see below). |

Two deliberate deviations from the v0.1.0 behavior follow from Core's v2 invariants, which are enforced by self-CI on every push:

1. **Least-privilege write.** Core forbids workflow-level write on these kernels (`tests/workflows/test_workflow_permissions.py`). Scorecard therefore does not publish to the code-scanning feed (`publish_results: false`, artifact output instead), and `release-publish.yml` validates and stages artifacts rather than publishing to PyPI/npm. Unattended publication belongs in a repo-owned workflow using trusted publishing. `nightly.yml` is the audited exception: its analyze job grants `checks: write` so the nested `analyze-semgrep.yml` publication can emit a GitHub check. Findings stay advisory on the nightly profile and are not a required merge check.
2. **Everything is SHA-pinned.** All external actions and nested Core workflows are pinned to full 40-character commit SHAs (`tests/architecture/test_external_action_pins.py`), and the gitleaks CLI is a version-pinned, checksum-verified binary rather than the gitleaks-action (which requires a license key on organization repositories).

The kernels never fail because a language toolchain is absent: each gate runs only when it applies to the repository, and inapplicable gates emit a `::notice` and pass. PR/push Semgrep stays on `analyze-semgrep.yml` via the v2 presets (`presets/*/.github/workflows/l9-analysis.yml`). Nightly deep analysis (`ci_deep`, advisory) is no longer a second product: it is the `nightly.yml` kernel at `profile: nightly`.

### Compatibility input behavior

Declared compatibility inputs are not promises that this frozen layer supports
every historical behavior. They are retained so old callers still resolve, but
their behavior is explicit:

| Workflow input | Neutral or disabled value | Non-neutral or enabled value |
|---|---|---|
| `pr-pipeline.yml`: `run-security` | `false` skips the security job. | `true` runs `security.yml` against the same working directory. |
| `pr-pipeline.yml`: `enable-pydantic-strict` | `false` is accepted. Pydantic typing remains repository-owned mypy configuration. | `true` fails during compatibility preflight because the shim does not implement this legacy switch. |
| `pr-pipeline.yml`: `changed-files` | The empty string is accepted. | A non-empty value fails during compatibility preflight because the shim does not implement changed-file routing. |
| `pr-pipeline.yml`: `pr-labels` | The empty string is accepted. | A non-empty value fails during compatibility preflight because the shim does not implement label routing. |
| `pr-pipeline.yml`: `labels-known` | `false` is accepted. | `true` fails during compatibility preflight because the shim does not consume PR labels. |
| `security.yml`: `run-npm-audit` | `false` always skips npm audit. | `true` runs npm audit for a detected Node project and fails explicitly if `package-lock.json` is absent. |

This is deliberately fail-closed for unsupported non-neutral values. An old
caller therefore receives an actionable error instead of a green run that
silently ignored the requested behavior. These shims do not add SDK analysis
semantics or a new consumer-governance surface.

## Tag policy

`v1` is a **moving compatibility tag**: it points at the newest main-branch commit that preserves the eight kernel contracts, and it may be advanced (never deleted) when the kernels receive backward-compatible fixes. Immutable point-in-time tags (`v1.0.0`, `v1.0.1`, ...) accompany each advancement. Callers that require bit-for-bit stability should pin the kernel by commit SHA, exactly as the v2 preset does for composite actions.

## Consumer guidance

Existing org-starter callers (`uses: Quantum-L9/l9-ci-core/.github/workflows/<kernel>.yml@v1`) now resolve without modification. New consumers should prefer the v2 presets under `presets/` for governed analysis and treat the v1 kernels as baseline hygiene gates. If a kernel input you pass is rejected, you are pinned to a pre-compat ref; move to `@v1` or a post-compat SHA.
