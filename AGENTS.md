# Agent Instructions
This repository is a thin control plane.
Before changing files:
1. Read `.l9/architecture.yaml`.
2. Read `.l9/ownership.yaml`.
3. Read `.l9/sdk-compatibility.yaml`.
4. Preserve the one-way dependency from Core to SDK.
5. Do not implement SDK-owned behavior in Core.
6. Do not introduce floating dependencies.
7. Do not add analysis workflows before Phase 2 is authorized.
8. Run the complete standard-library test suite.
A change that duplicates SDK behavior is invalid even when all functional tests
pass.

## SDK provisioning & the revision allowlist
`.github/actions/provision-sdk` is the only place Core executes the SDK. The set
of accepted SDK revisions is the allowlist in `.l9/sdk-compatibility.yaml`
(`default.revision` plus every `supported[].revision`); `provision.py` reads
that file as the single source of truth and fails closed on any unlisted,
branch, tag, or short revision, or a non-git source. When bumping the pin:
- update `default.revision` (and keep or add `supported[]` entries);
- keep the mirror copies in sync — `provision-sdk/action.yml`,
  `publish-analysis.yml`, `normalize-semgrep-report.yml`,
  `sdk-contract-check.yml`, and the `.l9` contract docs;
- pin only a commit SHA whose SDK `.l9/integration-contract.yaml` still exposes
  `semgrep normalize`, `bundle validate`, `bundle project-agent-payload`, and
  `compatibility check`. `sdk-contract-check.yml` verifies this on every PR.

## Cross-repo action references
Consumers call Core's actions and reusable workflows from a different
repository, so their checkout does not contain Core's `.github/actions/`.
Therefore:
- inside a composite action or a reusable workflow, reference a sibling Core
  action by its fully-qualified pinned form
  (`Quantum-L9/l9-ci-core/.github/actions/<name>@<sha>`), never a relative
  `uses: ./...` path — the relative form resolves against the caller's
  workspace and fails with "Can't find action.yml" for every consumer;
- a `run:`-based step that copies a file must tolerate source == destination
  (a consumer may already have written the artifact at its routed location).

## Governance surface
Governance files under `docs/templates/governance/` and consumer
`.github/governance/` use a `.yaml` extension but are parsed as JSON — keep them
valid JSON. Least-privilege `permissions` are mandatory on every workflow: only
the publication workflow may request `checks: write`; everything else is
`contents: read`. Pin external actions to full 40-char commit SHAs.

## MANIFEST
`MANIFEST.sha256` records the sha256 of tracked files; regenerate the entries
for any file you change so it stays honest.
