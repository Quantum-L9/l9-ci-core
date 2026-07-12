# Action Pin Inventory
## Purpose
`.github/governance/action-pins.lock.json` records the reviewed identity
of every external GitHub Action, remote reusable workflow, and container
action directly referenced by this repository.
The workflow reference is the execution source of truth. The inventory is
the review and audit record used to verify that the pinned identity was
resolved intentionally.
## Required entry types
### GitHub Action
```json
{
  "action": "actions/checkout",
  "kind": "action",
  "version": "v4.2.2",
  "commit_sha": "11bd71901bbe5b1630ceea73d27597364c9af683",
  "upstream_repository": "actions/checkout",
  "verification_method": "upstream-tag-resolution",
  "verified_at": "2026-07-11"
}

Reusable workflow

{
  "action": "owner/repository/.github/workflows/ci.yml",
  "kind": "reusable_workflow",
  "version": "v1.2.0",
  "commit_sha": "0123456789abcdef0123456789abcdef01234567",
  "upstream_repository": "owner/repository",
  "verification_method": "upstream-tag-resolution",
  "verified_at": "2026-07-11"
}

Docker action

{
  "action": "docker://alpine",
  "kind": "docker_action",
  "version": "3.20",
  "digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "verification_method": "verified-image-digest",
  "verified_at": "2026-07-11"
}

Update procedure

1. Identify the intended upstream release.
2. Resolve the release tag to its exact commit SHA or image digest.
3. Verify the upstream repository and release ownership.
4. Review the upstream change between the old and new immutable identities.
5. Update the workflow reference.
6. Update action-pins.lock.json.
7. Update the readable version annotation beside the workflow reference.
8. Run workflow/action-pins.
9. Run the complete bootstrap validator suite.
10. Review and merge through normal governance.

Required invariants

* Every external reference has one matching inventory entry.
* Every inventory SHA or digest matches the workflow.
* An empty inventory is valid only when the repository has no external references.
* A stale unused entry produces a warning.
* A mismatched or missing entry blocks.
* A missing readable version annotation warns but does not alter the SHA trust decision.
* No tag, branch, abbreviated SHA, or mutable container tag is accepted.

Validation

python .github/scripts/validate_action_pins.py
---
# 8. `docs/BOOTSTRAP_RUNTIME.md`
```markdown
# Trusted Bootstrap Runtime
## Purpose
The bootstrap runtime provides four local, deterministic validators that
protect the CI execution substrate before the declarative control plane is
introduced.
| Gate ID | Command |
|---|---|
| `workflow/action-pins` | `python .github/scripts/validate_action_pins.py` |
| `workflow/download-integrity` | `python .github/scripts/validate_download_integrity.py` |
| `dependencies/ci-lock` | `python .github/scripts/validate_ci_dependencies.py` |
| `workflow/contracts` | `python .github/scripts/validate_workflow_contracts.py` |
## Trust boundary
The bootstrap runtime verifies direct dependencies controlled by this
repository:
- external GitHub Action and reusable-workflow references
- container-action image digests
- executable or archive downloads made directly by workflows
- third-party Python packages explicitly installed by required CI jobs
- structural contracts of the repository's workflows
It does not claim to verify:
- GitHub-hosted runner images
- preinstalled system binaries
- GitHub's runtime infrastructure
- transitive internals of a pinned Action
- external services called by existing workflows
## Runtime
The bootstrap control-plane environment is fixed to:
```text
runner: ubuntu-latest
Python: 3.12

Consumer test Python versions remain independently configurable.

Base result contract

Each validator emits:

{
  "schema_version": "1.0",
  "gate_id": "workflow/action-pins",
  "result": "passed",
  "violations": [],
  "warnings": [],
  "metadata": {}
}

Status semantics

* passed: no violations; warnings are permitted.
* failed: one or more policy violations.
* error: parser, configuration, input, resource, or execution failure.

Exit codes

* 0: passed
* 1: policy violation
* 2: execution or configuration error

Complete-evidence execution

Run all validators:

python .github/scripts/run_bootstrap_validators.py \
  --root . \
  --output-dir .artifacts/bootstrap-results

Validate the complete result set:

python .github/scripts/validate_bootstrap_results.py \
  --root . \
  --results-dir .artifacts/bootstrap-results

A failure in one validator must not prevent the other validators from
running and writing evidence.

The complete set consists of:

action-pins.json
download-integrity.json
ci-dependencies.json
workflow-contracts.json
bootstrap-manifest.json

Missing, malformed, duplicate, schema-invalid, or gate-ID-mismatched
evidence fails the bootstrap job.

YAML parsing

All workflow inspection uses one YAML 1.2-compatible, comment-preserving,
source-location-preserving parser.

Required behavior:

* preserve on as a string
* reject duplicate keys
* reject unsupported YAML tags
* report source locations
* enforce byte and depth limits
* never construct arbitrary Python objects

Resource limits

Maximum workflow files:       200
Maximum workflow file bytes:  1,048,576
Maximum YAML depth:           64
Maximum run-block bytes:      262,144
Maximum registry entries:     500
Maximum result file bytes:    1,048,576
Maximum aggregate scan bytes: 25,165,824

Exceeding a limit is an error and exits with code 2.

Action pin updates

See docs/ACTION_PIN_INVENTORY.md.

Direct downloads

Every supported workflow download requires:

# l9-download: registry-key

The registry key must exist in:

.github/governance/download-integrity.yaml

The validator must prove:

1. the download is registered
2. the URL is the registered immutable URL
3. the expected SHA-256 is the registered digest
4. checksum verification targets the downloaded file
5. verification occurs before extraction, execution, or installation
6. streamed remote execution is absent

Unsupported download syntax fails closed.

Dependency locks

Repository-controlled dependencies must be installed with:

python -m pip install \
  --require-hashes \
  --requirement requirements/<profile>.lock

A local checked-out package may be installed with:

python -m pip install --no-deps -e .

Temporary exceptions belong in:

.github/governance/ci-dependency-exceptions.yaml

Every exception requires an owner, tracking issue, and expiry no more
than 30 days after creation.

Workflow contract debt

Historical workflow-contract violations may be deferred temporarily in:

.github/governance/workflow-contract-debt.yaml

A debt entry does not excuse new violations. It must identify the exact
workflow, job, step, rule, owner, reason, tracking issue, and expiry.

Public reusable-workflow compatibility

The public interface of:

.github/workflows/pr-pipeline.yml

is frozen in:

tests/fixtures/workflow-contracts/pr-pipeline-public-interface.json

Inputs may not be removed, renamed, type-changed, or made newly required
without a versioned major migration.

Future PR integration

workflow/action-pins, workflow/download-integrity,
dependencies/ci-lock, and workflow/contracts will be registered in
gate-registry.yaml.

The future planner will select these gate IDs. The generic executor will
invoke the existing commands. Canonical evidence will wrap the complete
bootstrap result rather than modifying it.

The validators remain permanently unaware of:

* enforcement mode
* risk tier
* pull-request number
* merge-group identity
* promotion result
* repository rulesets
* remediation agents

That separation is the runtime-primitive boundary.

---
# 9. CI dependency exception fixtures
## `tests/fixtures/ci-dependencies/valid-explicit-exception.yaml`
```yaml
schema_version: "1.0"
exceptions:
  - path: ".github/workflows/example.yml"
    line_or_step: "validate/install-legacy-tool"
    violation_code: "UNPINNED_PIP_INSTALL"
    reason: "Temporary compatibility bridge while the upstream wheel lock is generated."
    owner: "@quantum-l9/platform"
    created_at: "2026-07-11"
    expires_on: "2026-08-10"
    tracking_issue: "Quantum-L9/l9-ci-core#100"

tests/fixtures/ci-dependencies/expired-exception.yaml

schema_version: "1.0"
exceptions:
  - path: ".github/workflows/example.yml"
    line_or_step: "validate/install-legacy-tool"
    violation_code: "UNPINNED_PIP_INSTALL"
    reason: "Expired compatibility bridge that must no longer be honored."
    owner: "@quantum-l9/platform"
    created_at: "2026-05-01"
    expires_on: "2026-05-31"
    tracking_issue: "Quantum-L9/l9-ci-core#101"

tests/fixtures/ci-dependencies/missing-owner-exception.yaml

schema_version: "1.0"
exceptions:
  - path: ".github/workflows/example.yml"
    line_or_step: "validate/install-legacy-tool"
    violation_code: "UNPINNED_PIP_INSTALL"
    reason: "This fixture intentionally omits the accountable owner."
    created_at: "2026-07-11"
    expires_on: "2026-08-10"
    tracking_issue: "Quantum-L9/l9-ci-core#102"

tests/fixtures/ci-dependencies/wildcard-exception.yaml

schema_version: "1.0"
exceptions:
  - path: ".github/workflows/*.yml"
    line_or_step: "*"
    violation_code: "UNPINNED_PIP_INSTALL"
    reason: "This fixture intentionally attempts an overbroad wildcard exception."
    owner: "@quantum-l9/platform"
    created_at: "2026-07-11"
    expires_on: "2026-08-10"
    tracking_issue: "Quantum-L9/l9-ci-core#103"
