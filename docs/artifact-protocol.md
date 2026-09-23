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
SDK bundle validation + compatibility check
        |
        v
SDK agent-payload and SARIF projection
        |
        v
byte-preserving Core routing
        |
        v
Core gate/capability orchestration records
        |
        v
complete relocatable Core integrity index
        |
        v
immutable exact-name artifact upload
        |
        v
safe Core retrieval + complete verification
```

## Ownership

Raw provider reports are diagnostic inputs managed by Core. Canonical finding
bundles, agent-review payloads, and SARIF logs are generated and validated by
the SDK. Core may route or upload them byte for byte, but it may not modify,
reinterpret, merge, reconstruct, or reserialize them.

The Core integrity index is transport metadata, not another canonical artifact
format. Core reads file names, byte sizes, and bytes for SHA-256 hashing. It does
not inspect SDK-owned JSON structure or assign finding semantics.

## Complete relocatable integrity index

Before upload, `build-artifact-manifest` writes
`metadata/{matrix_id}/artifact-index.json` under the artifact root. Despite the
legacy action directory name, the emitted public schema is
`l9.core-artifact-index/v1`; no `l9.core-artifact-manifest/v1` document is
created.

The index covers **every regular file beneath the artifact root at index time**,
including raw reports, SDK-owned canonical/projection files, the Core routing
record, technical gate output, and repository-capability output. Each entry has
a root-relative POSIX path, byte size, and SHA-256 digest. Absolute paths,
parent traversal, symlinks, and non-regular files are rejected. The document
binds the set to the exact artifact name, analyzed repository and revision,
provider, matrix identifier, and SDK revision.

The index deliberately excludes only itself. A file cannot contain a digest of
its own final bytes without a non-standard fixed-point construction, so a
self-referential action SHA or self-digest is neither required nor fabricated.
The enclosing immutable GitHub artifact selected by exact name is the transport
identity; the index authenticates every other member after download.

Because every recorded route and entry is relative to `artifact_root: .`, an
artifact set may move from `artifacts/` during production to any safe download
directory without invalidating paths or digests.

## Safe routing and retrieval

`route-artifacts` confines sources and destinations to `GITHUB_WORKSPACE`,
rejects symlinked path components, copies SDK-owned bytes exactly, checks each
copy's digest, and emits workspace-relative outputs.

`.github/actions/retrieve-artifacts` is the Core retrieval primitive. It:

1. requires an empty non-symlink destination;
2. downloads one exact artifact name with a full-SHA-pinned
   `actions/download-artifact` action;
3. locates the fixed matrix-scoped integrity index;
4. verifies the requested artifact, repository, revision, provider, matrix, and
   SDK identities;
5. validates every indexed file's safe path, type, size, and SHA-256 digest;
6. rejects missing indexed files and any file not covered by the index; and
7. exposes bundle, payload, raw, routing-record, and optional SARIF paths only
   after complete verification.

The retrieval action does not run SDK validation or parse SDK formats. A caller
that consumes a finding bundle must still provision the allowlisted SDK and run
`bundle validate`; the retrieval step proves transport integrity and safe
routing, while the SDK remains the semantic authority.

## Matrix isolation and failure behavior

Every execution requires a stable matrix identifier. The identifier is
incorporated into filesystem paths, index paths, and artifact names to prevent
collisions between jobs, retries, profiles, language versions, and provider
configurations.

SDK exit codes propagate directly. Core does not convert validation,
compatibility, provider-report, strict-contract, or operational-limit failures
into generic success. Integrity, identity, path-safety, and completeness
failures are Core infrastructure/contract failures and fail closed regardless
of the provider's blocking, advisory, or shadow mode.
