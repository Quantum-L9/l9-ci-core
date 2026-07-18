# l9-ci-core v2.0.0

Clean-room rewrite of l9-ci-core as a **thin GitHub Actions control plane** with
a machine-enforced Core→SDK ownership boundary.

## Highlights

- **Thin control plane.** Core owns orchestration, immutable SDK provisioning,
  governance resolution, artifact routing, and publication. Analysis,
  canonical findings, gate computation, and schemas are owned by the SDK.
- **`.l9/` contract layer.** `architecture`, `ownership`, `governance-contract`,
  `publication-contract`, `artifact-protocol`, `sdk-compatibility`, `repo-spec`
  define — and tests enforce — what Core may and may not do.
- **Immutable SDK provisioning.** SDK pinned by full 40-char commit SHA
  (`c78486ea…`); floating refs, tags, branches, short SHAs, and arbitrary
  install commands are rejected (fail-closed).
- **Governance modes.** `blocking / advisory / shadow / disabled` with
  requiredness, time-boxed waivers, and a promotion policy.
- **Supply-chain integrity.** `MANIFEST.sha256`, SHA-pinned external actions,
  explicit least-privilege workflow permissions.
- **Boundary tests.** 55 architecture/governance/publication tests, including
  checks that forbid SDK-owned code and directories from appearing in Core.

## Breaking changes

- **Removed** the `pr-pipeline.yml@v1` reusable-workflow entrypoint. Consumers
  migrate to the v2 model (governance pack + `profile-normalize-semgrep.yml` /
  `publish-analysis.yml` + composite actions).
- Removed v1 PR-classification and label routing
  (`ci-routing-policy.yaml`, `l9-ci-shared-spec.yaml`, `label-taxonomy.yaml`),
  replaced by profile-driven governance.
- Generic lint/test (ruff/mypy/pytest, MegaLinter, Scorecard, SBOM, Gitleaks)
  is no longer bundled in Core; it is consumer-owned or SDK-owned.

## Consumer migration

See the CI instantiation pack and templates under
[`docs/templates/`](../templates/):

- `docs/templates/governance/` — the six-file governance pack to copy into
  `.github/governance/` (works for Python and Node.js).
- `docs/templates/l9-analysis.yml` — semgrep → SDK → publish caller.
- `docs/templates/l9-lint-test.yml` — Python lint/test hygiene.

Pin Core by `@v2.0.0` (immutable), `@v2` (moving alias), or a full commit SHA.

## Verify

```
python3 -m unittest discover --start-directory tests --pattern 'test_*.py'
```
