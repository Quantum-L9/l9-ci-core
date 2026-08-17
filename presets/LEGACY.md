# LEGACY — frozen language presets

> **Superseded by the organization-facing Core entrypoint.**

These per-language presets (`presets/python`, `presets/typescript`) and the
seeding skills (`skills/l9-ci-activation*`) implement the legacy copy-first
integration: an agent copies a preset's `.github/` tree into a consumer
repository.

That model is **legacy and frozen**:

- New consumers integrate through
  [`.github/workflows/org-ci.yml`](../.github/workflows/org-ci.yml), selected
  by l9-ci-control-plane at a full immutable Core commit SHA — no copied
  workflow, no copied governance pack, no consumer-owned Core revision.
- The identity maps under `presets/*/.github/governance/` are frozen
  snapshots: the registry-sync automation
  (`tools/regenerate_identity_maps.py` + `regenerate-identity-maps.yml`) was
  organization administration leakage and has been removed from Core.
- Presets receive no new features. Do not extend them for new consumers.
