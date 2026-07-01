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
```

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

## Secrets and permissions

PyPI publishing uses OIDC Trusted Publisher. No PyPI token is required when PyPI project trust is configured.

## Unknowns

Live GitHub runtime behavior remains Unknown until exercised in a caller repository.
