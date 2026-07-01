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
    uses: Quantum-L9/l9-ci-core/.github/workflows/nightly.yml@v1
    with:
      python-versions: "3.12"
      prerelease-python-versions: "3.13"
      source-dir: "."
      test-dir: "tests"
      coverage-threshold: "60"
```

## Gates

- `prepare-python-matrix`
- `nightly-regression-<python-version>`
- `nightly-summary`

## Unknowns

`actions/setup-python@v6` availability and Python 3.13 runtime behavior require live GitHub execution.
