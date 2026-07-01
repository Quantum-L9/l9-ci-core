<!-- L9_META
l9_schema: 1
origin: l9-ci-core
layer: [docs, governance, thresholds]
tags: [L9_TEMPLATE, thresholds, fail-closed]
owner: platform
status: active
/L9_META -->

# Threshold Governance

L9 coverage and security thresholds are governance policy, not workflow decoration.
Reusable workflows must not silently lower thresholds through caller inputs.

Authority file:

```text
.github/governance/quality-thresholds.yaml
```

Normal CI fails closed if the threshold policy is missing or malformed. The only
allowed internal fallback is the rock-bottom bootstrap floor used by `l9-ci init-repo`
while generating the initial governance files.

Required defaults:

- default coverage: 80
- l9-ci-sdk coverage: 85
- l9-ci-core coverage: 80
- max critical security findings: 0
- max high security findings: 0

Lowering thresholds requires an auditable governance file change and platform
approval through `l9-validated:approve`.
