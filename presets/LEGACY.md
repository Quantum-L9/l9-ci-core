# LEGACY — frozen language presets

> **Superseded by centrally required Core CI.**

The per-language presets and `l9-ci-activation*` seeding skills implement the
old copy-first model. They remain only as rollback/history until the central
required-workflow canary is proven.

For new consumers:

- GitHub organization rulesets require
  [`.github/workflows/org-ci.yml`](../.github/workflows/org-ci.yml) directly.
- Core centrally selects governance, SDK compatibility, tool pins, and
  enforcement behavior.
- The SDK detects repository capabilities from the actual checkout.
- A consumer may optionally add `.l9/ci.json` containing only `owner`,
  `repo_class`, and centrally issued `waiver_refs` pointers.
- Do not copy `.github/workflows/l9-analysis.yml` or `.github/governance/` into
  a new consumer.
- Do not distribute CI from `Quantum-L9/.github`.

Presets receive no new features. After central required-workflow validation,
remove the copied workflow/governance surfaces instead of evolving them.
