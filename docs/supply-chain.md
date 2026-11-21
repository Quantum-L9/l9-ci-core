# Supply chain (Core-owned, one mechanism)

`.github/actions/run-supply-chain` replaces the divergent `supply-chain.yml`
copies in Cursor-Governance, CEG and EIE with one Core mechanism and
per-repository policy data.

## When it runs

- **Pull request:** only when a dependency manifest or lock file changes
  (`pyproject.toml`, `uv.lock`, `requirements*.txt`, `package-lock.json`, ...,
  or `.l9/supply-chain-policy.json`). A change set that cannot be read runs.
- **Schedule, dispatch, push:** always.

## What it does

1. **Dependency review** (pull request only): `actions/dependency-review-action`
   v5.0.0 with Core's allow-list, the repository's package exemptions, and
   `fail-on-severity: high`.
2. **Isolated environment:** the locked runtime dependencies and every extra
   (`uv sync --locked --no-install-project --all-extras --no-default-groups`,
   with uv at Core's locked version); else `requirements.txt` or the
   `pyproject.toml` project in a fresh venv; else the tree as it stands.
3. **SBOM:** Syft (`anchore/sbom-action` v0.24.3), CycloneDX JSON of that
   environment, uploaded with the licence report for 90 days.
4. **Licence gate:** every package is judged against the merged policy. A
   denied licence, or a known licence that is not allowed, fails; an
   undetermined licence is reported as a warning; an SBOM with no packages
   fails closed. SPDX expressions are evaluated (`OR`: any allowed branch;
   `AND`: every term).

## Policy

- **Core** (`.github/actions/run-supply-chain/default-policy.json`): the allowed
  licences, the denied licences (GPL, AGPL, SSPL, EUPL), and aliases for the
  licence names Python metadata uses (`MIT License` -> `MIT`, ...).
- **Core, per repository** (`.github/actions/run-supply-chain/repositories/<owner>__<name>.json`,
  lower case, chosen by `$GITHUB_REPOSITORY`): exemptions Core grants one
  repository. Every entry carries a reason:

  ```json
  {
    "schema": "l9.supply-chain-core-repo-policy/v1",
    "repository": "Quantum-L9/<name>",
    "exemptions": [
      {"purl": "pkg:pypi/<package>", "license": "Proprietary", "reason": "first-party"}
    ],
    "additional_allowed_licenses": [{"license": "<SPDX id>", "reason": "..."}]
  }
  ```

  A Core-denied licence can never be allowed, and the file must name its own
  repository.
- **Repository** (`.l9/supply-chain-policy.json`, optional, merged additively):

  ```json
  {
    "schema": "l9.supply-chain-repo-policy/v1",
    "additional_allowed_licenses": [],
    "allow_dependencies_licenses": ["pkg:pypi/<package>"]
  }
  ```

  A repository may add allowed licences and exempt named packages (by purl).
  It can never allow a Core-denied licence; a malformed policy fails.

## Predicted result for the canaries (2026-11-20)

Run locally with the same steps (uv 0.13.8, Syft 1.54.0):

| Repository | Result | Exemption (now in Core's `repositories/` data) |
|---|---|---|
| l9-ci-core | pass (2 packages) | nothing |
| Cursor-Governance (`8c1f043`) | fails on `l9-graphite-memory` (Proprietary) | `pkg:pypi/l9-graphite-memory` |
| Cognitive.Engine.Graphs (`e1ccd1b`) | fails on `constellation-node-sdk` (Proprietary) | `pkg:pypi/constellation-node-sdk` |
| Enrichment.Inference.Engine (`4324e0d`) | fails on `constellation-node-sdk` (Proprietary) | `pkg:pypi/constellation-node-sdk` |

## Rollout

- Step one (this change): the action, its policy, and Core's own lane
  (`self-supply-chain.yml`: nightly, dispatch, and pull requests that change a
  dependency file).
- Step two: `org-ci.yml` runs the action, pinned at this change's merge commit
  (AGENTS.md §16), so every governed pull request that changes a dependency
  is judged. A consumer's nightly deep entry point calls the same action.
  The consumer copies are retired by their owners, after CURSOR_CANARY_PASS.
