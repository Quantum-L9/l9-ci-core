# L9-CORE-LEGACY-RETIREMENT-2026-10-22 — operator report

Repository `Quantum-L9/l9-ci-core` · base `main` = `80c0cac4223d6ef0e001b53e8174be6cacd96f0f`.

## Why now

`.l9/release-plane.yaml` and `AGENTS.md` §7 froze the copy-first surfaces
"until the central required-workflow canary is proven". Live evidence
(2026-10-22, read-only REST):

| Fact | Evidence |
|---|---|
| Org ruleset `L9 canonical CI required` (21895545) is `active` on the default branch of `l9-ci-core`, `l9-ci-sdk`, `l9-deploy`, `l9-semantic-compiler-engine` | `repos/<r>/rules/branches/main` on each |
| It requires `Quantum-L9/l9-ci-core` `refs/heads/main` `.github/workflows/org-ci.yml` and check `Analyze (central Core)` | same |
| Latest `Analyze (central Core)` run on each repository: `success` (push on Core; `pull_request` on the three consumers) | `actions/runs` per repository |
| Control-plane attestation on Core `main`: binding PASS; main protection FAIL; freshness FAIL (strict policy false); immutable releases UNKNOWN (token) | run 39755536334 |

The canary is proven; the frozen surfaces are removed rather than evolved.

## Removed (git history keeps them)

- `presets/python/**`, `presets/typescript/.github/**`, `starter-workflows/**`,
  `docs/templates/**`, `skills/l9-ci-activation*/**` — copy-first callers
  with `L9_CORE_REF` pins and governance-pack copies.
- `.github/workflows/{pr-pipeline,pre-commit-ci,nightly,release-publish,trio-governance,scorecard,sbom,baseline-ratchet}.yml`
  — v1 reusable kernels and a consumer-concept ratchet with no Core caller.
- `.github/governance/**` — dead duplicate of the bundled defaults that
  overrode `pr_fast`/`merge` Semgrep to `advisory`; no runtime path read it.
- `docs/central-ci-bridge/**`, `docs/org-runtime/baseline-inventory.md`,
  `docs/v1-compatibility.md`, `docs/pipeline-runs/**` (2.1 MB of external
  run evidence).

## Kept, deliberately

`presets/typescript/{biome.json,stamp.sh}` (the locked consumer Biome
contract), `security.yml` (dogfooded by `self-security.yml`),
`analyze-semgrep.yml` + `publish-analysis.yml` (self-analysis dogfood and the
bounded Cognitive Runtime `v2` channel), and the Phase 2/4 reusable surfaces
`normalize-semgrep-report.yml`, `profile-normalize-semgrep.yml`,
`publish-sarif.yml` (declared in `.l9/publication-contract.yaml`).

## Superseded open pull requests

These Dependabot PRs bump files this change deletes. They become empty on
merge and should be closed, not merged:

| PR | Head branch | File it bumps |
|---|---|---|
| #208 | `dependabot/github_actions/Quantum-L9/l9-ci-core/dot-github/actions/install-consumer-ci-48514e…` | `.github/workflows/pr-pipeline.yml` |
| #214 | `dependabot/github_actions/github-actions-minor-patch-30f38afe1e` | `.github/workflows/sbom.yml`, `.github/workflows/scorecard.yml` |

The publish gate's overlap probe named exactly these two; publication
proceeded under `PR_OVERLAP=ignore` with this justification: the overlap is
a deletion of the files the sibling PRs edit, so no textual resolution exists
and the siblings are moot.

## Verification executed on the retired tree

`make validate` PASS · `make check` PASS (ruff 0.16.1, mypy 2.3.0) ·
`make change-policy` selected gate `workflow`, no companion findings ·
`make agent-check` PASS (full `unittest` suite) ·
`tests/workflows` 224 OK, `tests/governance` 37 OK, `tests/actions` 72 OK,
`tests/architecture` 4 OK.
