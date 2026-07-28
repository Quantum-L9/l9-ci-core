# l9-ci-core
GitHub Actions control plane for the Quantum-L9 CI platform.
**Version:** 2.0.0  
**Default SDK:** `Quantum-L9/l9-ci-sdk@0c487747b0fcd172edaefe9e843dac818de8fc12`
## Ownership boundary
Core orchestrates:
- workflow execution;
- governance resolution;
- provider runtime provisioning;
- immutable SDK provisioning;
- SDK command invocation;
- artifact routing and upload;
- retention;
- GitHub check publication;
- workflow enforcement.
The SDK owns:
- provider adapters;
- provider-report normalization;
- canonical evidence and findings;
- validation;
- classification;
- canonical gate evaluation;
- downstream projections.
Core publishes the SDK-produced gate decision. It does not reconstruct a
verdict from provider exit codes, finding counts, raw reports, or projected
payloads.
## Authoritative Semgrep workflow
`.github/workflows/analyze-semgrep.yml` owns the complete Semgrep pipeline:
```text
checkout caller revision
  → resolve Core governance
  → install exact Semgrep runtime
  → provision one immutable SDK revision
  → SDK provider execution and normalization
  → SDK bundle validation
  → SDK gate evaluation
  → SDK agent-review projection
  → Core artifact routing
  → Core manifest construction
  → artifact upload
  → publication
  → blocking-mode enforcement

Consumer repositories should call this workflow directly by immutable Core
commit.

jobs:
  analysis:
    uses: Quantum-L9/l9-ci-core/.github/workflows/analyze-semgrep.yml@<CORE_SHA>
    with:
      profile: pr_fast
      matrix-id: pr-semgrep
      language: python
      semgrep-version: "1.171.0"
      governance-root: .github/governance
      repository-revision: ${{ github.sha }}
      retention-days: 14
      publish: true

The consumer workflow owns only:

* event triggers;
* concurrency;
* minimum permissions;
* profile and matrix selection;
* the immutable Core commit pin.

SDK compatibility authority

.l9/sdk-compatibility.yaml is the Core-owned SDK allowlist and handoff
registry.

It defines:

* the default SDK revision;
* supported rollback revisions;
* required integration contracts;
* required artifact protocols;
* required CLI paths;
* supported languages and execution profiles;
* the authoritative reusable workflow;
* drift-prevention policy.

One analysis run must use the same SDK revision for provider execution,
normalization, validation, gate evaluation, projection, and publication
revalidation.

Compatibility workflows

The following workflows remain available for existing consumers:

Workflow	Purpose
pr-pipeline.yml	Language-aware lint, type-check, and test compatibility pipeline
nightly.yml	Language-aware nightly tests and dependency reporting
normalize-semgrep-report.yml	Imports a caller-produced Semgrep report through the SDK
profile-normalize-semgrep.yml	Governance wrapper around report import
publish-analysis.yml	Publishes an uploaded SDK artifact set

pr-pipeline.yml and nightly.yml can optionally invoke the authoritative
analysis workflow after their existing validation jobs. Optional nested
analysis is artifact-only; repositories requiring publication should call
analyze-semgrep.yml directly with the required check permissions.

Artifact set

Every completed Semgrep analysis uploads:

artifacts/raw/semgrep/<matrix-id>/report.json
artifacts/l9/<matrix-id>/finding-bundle.json
artifacts/l9/<matrix-id>/gate-result.json
artifacts/l9/<matrix-id>/agent-review-payload.json
artifacts/metadata/<matrix-id>/artifact-manifest.json

Core routes canonical artifacts without modifying their bytes. The manifest
records their paths and SHA-256 digests.

Local validation

python -m pip install -r requirements-ci.txt
python -m pytest -q
python tools/check_workflow_integrity.py

Repository contracts

* .l9/sdk-compatibility.yaml
* .l9/artifact-protocol.yaml
* .l9/governance-contract.yaml
* .l9/publication-contract.yaml
* .l9/ownership.yaml
* .github/workflows/analyze-semgrep.yml
