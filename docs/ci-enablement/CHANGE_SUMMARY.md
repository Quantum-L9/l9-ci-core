# CHANGE_SUMMARY — CI enablement (l9-ci-core)

## What lands

- `pr-checks.yml`: quality/security gate for this repo — blocking `pytest` (the
  30 governance/workflow/classifier tests) with coverage; advisory `ruff`
  (clean today), `mypy`, and Sonar; GitGuardian blocking when its secret is
  visible, skipped otherwise.
- `agent-payload-contract.yml`: validates that
  `schemas/agent-review-payload.schema.json` is a valid JSON Schema (always,
  blocking) and — when `L9_CI_INSTALL_SPEC` is set — generates a real payload
  with `l9-ci` and validates it against that schema. Fills the gap: the reusable
  `pr-pipeline.yml` emits the payload but never validated it.
- `pr-repair.yml`: emits the payload (installing `l9-ci` via the spec) and
  optionally hands off to `Quantum-L9/PR_Repair`. Bot NOT vendored.
- CodeRabbit config, Sonar mapping, `AGENT.md`.

## Impact

- Core PRs now get lint/type/test signal + a self-check that the payload schema
  it owns stays valid and honored by the SDK.
- No change to the reusable workflows' behavior; no source changes; no merges;
  no settings changes.

## Blocking-vs-advisory rationale (measured locally)

| Gate | Status today | Decision |
|---|---|---|
| pytest | 30 passing (classify_pr 70% cov) | **blocking** |
| schema meta-validation | schema valid (Draft 2020-12, 14 required fields) | **blocking** |
| ruff | clean | advisory (kept non-blocking; no committed ruleset baseline) |
| mypy | pre-existing findings | advisory |
| GitGuardian | secret visibility unconfirmed | blocking when present, else skipped |
| Sonar | projectKey/org UNKNOWN | advisory |
