# L9 Trio Wiring Contract

## Purpose

This pack defines CI-side wiring expectations for the Audit, Implementer, and Validator trio. It does not ship private tokens, app IDs, or repo-specific branch protection because those belong to each consuming repository.

## Required Bots

- `l9-audit-bot`: code audit plane
- `l9-implementer-bot`: implementation plane
- `l9-validator-bot`: validation plane
- `l9-ci-audit-bot`: CI governance audit plane

The Implementer and Validator must use distinct GitHub Apps or distinct tokens. Implementer self-approval is forbidden.

## Required Merge Signal

Branch protection in the consuming repo should require the Validator-owned merge signal:

```text
l9-validated:approve
```

## Marker Separation

CI uses:

```html
<!-- l9-ci-audit-marker: v1 -->
```

Forbidden collisions:

```html
<!-- l9-audit-marker: v1 -->
<!-- l9-implementer-marker: v1 -->
<!-- l9-validator-marker: v1 -->
```

## CI Enforcement Boundary

CI must verify that contract controls are wired and fail-closed. CI does not rewrite application code.
