# Release Workflow

## Purpose

`release-publish.yml` is a reusable L9 release gate for Python packages. It harvests the source pack release sequence but normalizes it for universal use.

## Source harvest

Harvested concepts:
- semver tag and `pyproject.toml` version consistency
- release quality gate
- dependency audit
- sdist/wheel build and `twine check`
- CycloneDX SBOM
- PyPI Trusted Publisher/OIDC publishing
- GitHub Release attachment
- `all-release-gates-passed` aggregator

Rejected:
- raw Gate_SDK paths
- superseded action majors `actions/checkout@v4` / `actions/setup-python@v5` (pinned to `@v6`)
- hardcoded schema/script locations
- release behavior without explicit publish enablement

## Caller example

```yaml
name: release
on:
  push:
    tags: ["v*.*.*"]

jobs:
  release:
    uses: Quantum-L9/l9-ci-core/.github/workflows/release-publish.yml@v1
    with:
      python-version: "3.12"
      source-dir: "."
      test-dir: "tests"
      enable-pypi-publish: true
      pypi-publish-mode: "api-token"
    secrets:
      pypi-api-token: ${{ secrets.PYPI_API_TOKEN }}
```

> Note: this repo (`l9-ci-core`) always calls `release-publish.yml` via
> `workflow_call` from a consumer repo — that is the entire point of it
> being a reusable workflow. See **PyPI publish modes** below for why this
> means `pypi-publish-mode: api-token` is the correct default choice for
> essentially every real caller, not `trusted-publisher`.

## Gates

1. `gate-1-tag-version-check`
2. `gate-2-quality`
3. `gate-3-dep-audit`
4. `gate-4-build`
5. `gate-5-cyclonedx-sbom`
6. optional `gate-6-clean-install-smoke`
7. optional `gate-7-pypi-publish`
8. optional `gate-8-github-release`
9. `all-release-gates-passed`

## PyPI publish modes

`gate-7-pypi-publish` supports two mutually exclusive authentication modes, selected via the `pypi-publish-mode` input:

| Mode | How it authenticates | Works when... |
|---|---|---|
| `trusted-publisher` (default) | OIDC — GitHub mints a short-lived identity token, PyPI verifies it against a pre-registered trusted publisher (repo + workflow filename + ref) | This workflow is invoked **directly** by the caller repo's own workflow file (not via `workflow_call`). PyPI's trust binds to the exact calling workflow, so a nested `workflow_call` invocation from `l9-ci-core/.github/workflows/release-publish.yml` presents the wrong identity and publish will fail authentication. |
| `api-token` | Caller-supplied `secrets.pypi-api-token`, passed as `password` to `pypa/gh-action-pypi-publish` | Any caller, including every real consumer of this reusable workflow (since consumers invoke it via `workflow_call` by design — see the Caller example above). **This is the recommended mode for essentially all callers of `release-publish.yml`.** |

Because every consumer of this file calls it via `workflow_call` (that's the entire purpose of packaging it as a reusable workflow), `trusted-publisher` mode will not work as originally documented for any real caller and should be treated as a legacy/experimental option retained only for repos that copy the publish step directly rather than calling this file. `api-token` is the supported path.

### Configuring `api-token` mode

1. On [pypi.org](https://pypi.org/manage/account/token/), generate an API token scoped to the single project being published — never an account-wide token.
2. Store it as a secret in the consumer repo, ideally under the `pypi` GitHub Environment (matching the `environment: pypi` already declared on the `publish` job) so environment protection rules (required reviewers, wait timers) apply to it.
3. Pass `pypi-publish-mode: "api-token"` and `secrets: { pypi-api-token: ${{ secrets.PYPI_API_TOKEN }} }` in the caller workflow, as shown above.
4. Rotate the token periodically; `gate-7-pypi-publish` will fail closed with an explicit error if the mode is set to `api-token` but no secret is supplied, rather than silently falling through.

## Unknowns

The `api-token` fallback path has not yet been exercised against a live PyPI publish from a real consumer repository — recommend validating on the next scheduled release before removing this note.
