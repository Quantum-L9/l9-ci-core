# Artifact Protocol
## Pipeline
```text
provider-native report
        |
        v
SDK normalization
        |
        v
canonical finding bundle
        |
        v
SDK bundle validation
        |
        v
SDK compatibility check
        |
        v
SDK agent-payload projection
        |
        v
byte-preserving Core routing
        |
        v
Core artifact manifest
        |
        v
immutable artifact upload

Ownership

Raw provider reports are diagnostic inputs managed by Core.

Canonical finding bundles and agent-review payloads are generated and
validated by the SDK. Core may route or upload them but may not modify,
reinterpret, merge, or reconstruct them.

Matrix isolation

Every execution requires a stable matrix-id. The identifier is incorporated
into filesystem paths and artifact names to prevent collisions between jobs,
retries, profiles, language versions, and provider configurations.

Failure behavior

SDK exit codes propagate directly. Core does not convert validation,
compatibility, provider-report, strict-contract, or operational-limit failures
into generic success or generic gate results.
