# LEGACY — frozen copy-first distribution

> **Superseded by the organization-facing Core entrypoint.**

The copy-first integration model documented here is **legacy and frozen**.
Do not extend it, and do not teach it as the integration path.

- The single live organization entrypoint is
  [`.github/workflows/org-ci.yml`](../../.github/workflows/org-ci.yml)
  (`l9.org-runtime-contract/v1`, declared in
  [`.l9/org-runtime-contract.yaml`](../../.l9/org-runtime-contract.yaml)).
- l9-ci-control-plane selects that workflow at a full immutable Core commit
  SHA and delivers the governance pack as the `governance` input. When the
  input is empty, Core applies its own bounded standard defaults
  (`.github/org-governance-defaults/`).
- The governance files in this directory, `l9-analysis.yml`, and the hygiene
  templates remain only for historical consumers still on the copy path.
  They are no longer Core's distribution mechanism and receive no new
  features.
