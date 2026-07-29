# l9-ci-core

Thin GitHub Actions control plane for the Quantum-L9 CI platform.

**Version:** 2.0.0  
**Pinned SDK:** `Quantum-L9/l9-ci-sdk@0c487747b0fcd172edaefe9e843dac818de8fc12`

The four implemented phases provide immutable SDK provisioning, validated
artifact routing, governance resolution, and publication through workflow
summaries and GitHub checks. Core publishes the SDK-owned agent-review
projection and never reconstructs canonical findings or gate outcomes.

## Repository execution runtime

Artifact ID: `l9-ci-core-repository-execution-runtime`
Artifact version: `4.3.0`

Local repository execution is governed by the L9 Core Repository Execution
Runtime v4.3.0: a deterministic execution governor that compiles repository
policy and Git state into bounded checks, guarded mutation, and inspectable
evidence. Read `AUTHORITY.md` first; executable policy lives in
`.l9/repo-workflow.json`, and the root `Makefile` is a generated adapter,
not an authority source.

```bash
make setup
make validate
make change-policy
make agent-check
make status
```

`agent-check` is the single non-mutating completion proof. Operator guidance
lives in `OPERATIONS.md`; the validation contract lives in `VALIDATION.md`.
