# Publication Plane

## Flow

```text
immutable uploaded artifact
        |
        v
download into isolated publication job
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
workflow summary + SARIF upload + GitHub check upsert
```

The publication job does not execute repository source code. It downloads an
immutable artifact, provisions the pinned SDK, validates the canonical bundle,
and consumes the SDK-generated agent-review and SARIF projections. Before the
first remote publication side effect, Core validates the local Checks API
envelope and the optional SARIF transport envelope. SARIF finding semantics
remain SDK-owned and are not parsed by Core.

Blocking failures publish as failed checks. Advisory failures may publish as
neutral. Shadow and disabled modes create no check. Core accepts only
annotations already present in the SDK projection and sends at most 50 in one
check-run request. Each check uses the deterministic external identity
`l9.core-publication/v1:<workflow-run-id>:<matrix-id>`. The workflow run ID is
stable across GitHub reruns, so a retry updates the matching check run rather
than creating a duplicate; the run-attempt number is intentionally excluded.
