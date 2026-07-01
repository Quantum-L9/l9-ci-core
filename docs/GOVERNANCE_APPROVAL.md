<!-- L9_META
l9_schema: 1
origin: l9-ci-core
layer: [docs, governance, trio]
tags: [L9_TEMPLATE, approval, trio, fail-closed]
owner: platform
status: active
/L9_META -->

# Governance Approval

Branch protection is not the sole governance authority. `l9-ci gate` enforces
approval for protected governance changes so repository admins cannot quietly
bypass policy by lowering thresholds or editing CI governance files.

Protected changes include:

- `.github/governance/**`
- `.github/scripts/**`
- `.github/workflows/pr-pipeline.yml`
- `.github/workflows/trio-governance.yml`
- `l9-ci-core/.github/governance/**`
- `l9-ci-core/.github/workflows/**`

If protected files change, `l9-ci gate` requires the PR label:

```text
l9-validated:approve
```

If labels are unknown in CI and protected files changed, the gate fails closed.
