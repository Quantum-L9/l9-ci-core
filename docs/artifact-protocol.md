# Artifact Protocol

## Pipeline

```text
repository snapshot
        |
        v
SDK-owned bounded provider execution
        |
        v
provider-native report
        |
        v
SDK normalization + validation
        |
        v
canonical finding bundle
        |
        +--> SDK agent-review projection
        |
        +--> SDK gate evaluation
                 |
                 v
          canonical gate-result.json
        |
        v
byte-preserving Core routing
        |
        v
Core artifact manifest binds bundle + agent payload + gate result
        |
        v
immutable artifact upload
        |
        v
Core re-evaluates bundle and byte-compares gate result
        |
        v
Core publishes the SDK gate status under governance mode
```

## Ownership

Raw provider reports are diagnostic inputs managed by Core. Provider execution is
invoked through the SDK public CLI so timeout, output limits, version policy,
configuration validation, and structured failures are applied by one lifecycle.

Canonical finding bundles, agent-review payloads, and gate results are generated
and validated by the SDK. Core may route, hash, upload, re-evaluate for integrity,
and publish them. Core must not modify, reinterpret, merge, or reconstruct their
findings or verdicts.

## Publication semantics

The canonical gate status is the verdict authority:

- `pass`: successful publication.
- `fail`: failure in blocking mode; neutral in advisory mode.
- `incomplete`: failure in blocking mode; neutral in advisory mode.
- `invalid`: failure in blocking mode; neutral in advisory mode.

Infrastructure failure or cancellation remains visible and is never converted to
a canonical gate result. `shadow` and `disabled` modes retain artifacts without
publishing a GitHub check.

## Matrix isolation

Every execution requires a stable matrix-id. The identifier is incorporated into
filesystem paths and artifact names to prevent collisions between jobs, retries,
profiles, language versions, and provider configurations.

## Failure behavior

SDK provider, validation, compatibility, and operational-limit exit codes
propagate directly. Gate semantic exit codes are accepted only when the SDK wrote
a structurally valid `l9.gate-result/v1` artifact whose status matches the exit
code. Core then routes and publishes that artifact rather than treating a FAIL or
INCOMPLETE verdict as a broken workflow.
