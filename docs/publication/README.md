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
workflow summary + GitHub check
```

The publication job does not execute repository source code. It downloads an
immutable artifact, provisions the pinned SDK, validates the canonical bundle,
and consumes the SDK-generated agent-review projection.

Blocking failures publish as failed checks. Advisory failures may publish as
neutral. Shadow and disabled modes create no check. Core accepts only
annotations already present in the SDK projection and sends at most 50 in one
check-run request.
