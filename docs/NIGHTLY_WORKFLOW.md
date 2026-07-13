# Nightly Workflow

## Purpose

`nightly.yml` is a reusable nightly regression gate. Caller repositories own the schedule; `l9-ci-core` owns the normalized job logic.

## Source harvest

Harvested concepts:
- daily regression pattern
- strict Python matrix plus forward-compatible prerelease Python versions
- coverage threshold gate
- clean-install import smoke
- contract spec lint
- schema drift pattern
- strict `pip-audit`
- optional verified TruffleHog scan
- nightly summary gate

Rejected:
- hardcoded `src`, `scripts`, `contracts`, and schema paths
- raw branch assumptions
- raw Gate_SDK workflow identity

## Caller example

```yaml
name: nightly
on:
  schedule:
    - cron: "17 8 * * *"
  workflow_dispatch:

jobs:
  nightly:
    # Pin the reusable workflow to a 40-character commit SHA (a release tag such
    # as `@v1` is mutable and can be moved after review). The `# v1.x.y` comment
    # records the human-readable release the SHA corresponds to.
    uses: Quantum-L9/l9-ci-core/.github/workflows/nightly.yml@<40-char-commit-sha> # v1.x.y
    with:
      python-versions: "3.12"
      prerelease-python-versions: "3.13"
      source-dir: "."
      test-dir: "tests"
```

## Gates

- `prepare-python-matrix`
- `nightly-regression-<python-version>`
- `all-gates-passed`

## Unknowns

The workflows pin `actions/checkout` and `actions/setup-python` to full 40-character commit SHAs (with the resolved major recorded in a trailing comment), per [ADR-001](adr-001-sha-pinning.md); mutable major tags such as `@v6` are never used. Python 3.13 prerelease runtime behavior is the remaining unknown that requires live GitHub execution.
