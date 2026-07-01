# Pre-Commit CI Workflow

## Purpose

`pre-commit-ci.yml` validates the repository's pre-commit surface in CI. This complements local hooks and the main PR pipeline without duplicating the PR pipeline's direct tool checks.

## Source harvest

Harvested concepts:
- pre-commit config validation
- `pre-commit run --all-files`
- hook cache restore
- hook version drift warnings
- `pre-commit-passed` branch-protection aggregator

Rejected:
- hardcoded `main`/`master` triggers inside the core reusable workflow
- hardcoded Gate_SDK local hook assumptions
- raw checkout/setup-python v4/v5 action versions

## Caller example

```yaml
name: pre-commit
on:
  pull_request:
  push:
    branches: [main]

jobs:
  pre_commit:
    uses: Quantum-L9/l9-ci-core/.github/workflows/pre-commit-ci.yml@v1
    with:
      python-version: "3.12"
      config-path: ".pre-commit-config.yaml"
```

## Gates

- `validate-hook-config`
- `pre-commit-run-all-files`
- optional `hook-version-drift-check`
- `pre-commit-passed`

## Unknowns

Remote hook installation and cache behavior must be confirmed in a caller repository.
