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
closed Core handoff descriptor (after upload succeeds)
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

## Versioned cross-run handoff

After `actions/upload-artifact` succeeds, actual Phase 2 producers may invoke
`create-artifact-handoff` to emit canonical `l9.core-artifact-handoff/v1` JSON.
The descriptor is an **additive Core transport output**. Consumers are not
required to accept it, and it neither replaces nor weakens the SDK-owned bundle
and projection formats or the complete Core integrity index inside the uploaded
artifact.

The schema is closed. It binds the producer repository and workflow run, the
immutable GitHub artifact ID and exact name, the SHA-256 digest of the uploaded
archive, the analyzed repository and full revision, provider and matrix
identifier, and the exact SDK repository, revision, and integration contract.
It contains no artifact URL and no finding, raw-report, SARIF, bundle, or route
semantics. Descriptor bytes use UTF-8, lexicographically sorted compact JSON,
and one trailing LF. The producer rejects malformed identities and missing,
unsafe, existing, or symlinked destinations rather than overwriting stale state.

The descriptor is created outside the already uploaded artifact tree. Therefore
it can bind the immutable server-assigned artifact ID and archive digest without
creating a self-reference or changing the indexed content after upload.

## Safe routing and retrieval

`route-artifacts` confines sources and destinations to `GITHUB_WORKSPACE`,
rejects symlinked path components, copies SDK-owned bytes exactly, checks each
copy's digest, and emits workspace-relative outputs.

`.github/actions/retrieve-artifacts` is the Core retrieval primitive. It has two
mutually exclusive modes. Existing callers use **current-run exact-name mode**
with the same artifact name and expected index identities as before. A later
cross-run caller may instead pass the canonical handoff descriptor and a token
with Actions read access; no descriptor is required of current consumers.

In current-run mode, the action:

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

In descriptor mode, before any download the action parses the closed canonical
document and uses only its producer repository, run ID, and artifact ID as the
source. There are deliberately no caller inputs for source repository, source
run, artifact ID, or artifact URL. It queries GitHub's artifact metadata endpoint
with the required token and verifies the immutable ID and name, producing run,
workflow head revision, unexpired lifecycle timestamps, and SHA-256 archive
digest. Only then does it invoke the pinned downloader by immutable artifact ID.
After download, it runs the exact same complete index, tree, identity, digest,
and route verification used by current-run mode before exposing outputs.

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
