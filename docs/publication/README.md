# Publication Plane

## Flow

```text
immutable uploaded artifact + handoff descriptor
        |
        v
verified descriptor lookup and download into an isolated publication job
        |
        v
SDK bundle validation
        |
        v
consume SDK agent-review projection
        |
        v
render bounded Core publication envelope
        |
        v
locally preflight publication + optional SARIF envelopes
        |
        v
workflow summary + independently elected SARIF upload or GitHub check upsert
```

The publication job does not execute repository source code. It downloads an
immutable artifact, provisions the pinned SDK, validates the canonical bundle,
and consumes the SDK-generated agent-review and SARIF projections. Before the
first remote publication side effect, Core validates the local Checks API
envelope and the optional SARIF transport envelope. SARIF finding semantics
remain SDK-owned and are not parsed by Core. The preferred cross-run transport
is the closed `l9.core-artifact-handoff/v1` descriptor: it binds the producer
repository, run, immutable artifact ID, archive digest, subject revision,
provider, matrix, and SDK revision before retrieval. The compatibility
current-run exact-name path remains available when a caller has no descriptor.

## Capability Boundary

Direct analysis always runs with **`contents: read`** and writes an immutable
artifact plus descriptor; it neither creates GitHub checks nor uploads SARIF.
Check publication and SARIF publication are independently elected reusable
capabilities that default to `false`. The former receives only `checks: write`;
the latter receives only `security-events: write`. This makes publication an
auditable opt-in rather than a permission inherited by every consumer.

A trusted Core self-analysis caller can elect a terminal check after an
evidence-ready blocking failure, so the SDK verdict remains visible even though
the analysis job deliberately exits non-zero. Fork pull requests are excluded
from that write-scoped route, and publication jobs never use
`pull_request_target` or check out the analyzed source.

Blocking failures publish as failed checks. Advisory failures may publish as
neutral. Shadow and disabled modes create no check. Core accepts only
annotations already present in the SDK projection and sends at most 50 in one
check-run request. Each check uses the deterministic external identity
`l9.core-publication/v1:<workflow-run-id>:<matrix-id>`. The workflow run ID is
stable across GitHub reruns, so a retry updates the matching check run rather
than creating a duplicate; the run-attempt number is intentionally excluded.
