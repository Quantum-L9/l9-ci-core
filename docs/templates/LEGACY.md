# LEGACY — frozen copy-first distribution

> **Superseded by centrally required Core CI.**

The copy-first integration model documented in this directory is legacy and
frozen. Do not extend it, seed it from `Quantum-L9/.github`, or teach it as the
integration path.

The active architecture is:

- GitHub organization rulesets target repositories and require
  [`.github/workflows/org-ci.yml`](../../.github/workflows/org-ci.yml).
- `l9-ci-core` owns central orchestration, governance defaults, SDK/tool pins,
  enforcement, routing, and publication.
- `l9-ci-sdk` owns repository capability detection, provider execution,
  canonical evidence/findings, technical gate evaluation, and projections.
- Consumer repositories may optionally provide `.l9/ci.json` with only
  ownership, repo-class, and centrally issued waiver pointers.
- No copied L9 workflow, governance pack, Core pin, or SDK pin is required in a
  consumer repository.

Files in this directory remain only for historical consumers until the central
required-workflow canary is proven. They receive no new features and must be
removed rather than evolved after the replacement path is validated.
