<!-- L9_META
l9_schema: 1
origin: l9-ci-universal-base
layer: [docs, ci]
tags: [L9_TEMPLATE, local-first, pipeline, matrix-safe]
owner: platform
status: active
/L9_META -->

# Local-First Pipeline Runner

`l9-ci run-pipeline` is the SDK-owned execution surface for local developer runs, GitHub Actions, and future agent sandboxes.

GitHub Actions remains the runner shell. The SDK owns stage semantics, governance decisions, matrix-safe summary output, and gate evidence.

## Stageable execution

```bash
l9-ci run-pipeline --stage classify
l9-ci run-pipeline --stage validate
l9-ci run-pipeline --stage test
l9-ci run-pipeline --stage gate
```

## Matrix-safe artifacts

Matrix jobs must not write to the same artifact path. Use `--emit-dir` or provide a matrix-specific filename.

```bash
l9-ci run-pipeline --stage test --matrix python=3.12 --emit-dir artifacts/ci
```

This produces:

```text
artifacts/ci/test_python-3-12_ci_summary.json
```

If `--matrix` is present and `--emit-json` does not contain the matrix identifier, the SDK fails closed.

## Why

This preserves GitHub matrix parallelism while making pipeline logic portable across local shells, GitHub Actions, Buildkite, GitLab, and bounded agent execution.
