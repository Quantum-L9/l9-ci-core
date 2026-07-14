# l9-ci-core

Reusable GitHub Actions workflows for L9 repositories.

## Python workflow

```yaml
jobs:
  pr_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/pr-pipeline.yml@v1
    with:
      python-version: "3.12"
      source-dir: "."
      test-dir: "tests/"
```

The workflow installs the SDK as a runtime CLI through the `l9-ci-install-command` input. The default now installs the SDK from the SDK repository pinned to commit `d32e84b` via `git+https` (`python -m pip install "l9-ci @ git+https://github.com/Quantum-L9/l9-ci-sdk.git@d32e84b7c00fc88b85f2639471dd64126251e09e"`). Callers can override this input to pin a different ref or install source.
Private-repo callers set an `SDK_TOKEN` secret granting read access to `Quantum-L9/l9-ci-sdk`; public callers need no token. Once the SDK is published to an index, the default will switch back to `pip install l9-ci`. See [docs/SDK_INSTALL.md](docs/SDK_INSTALL.md) for details.

## Node.js and TypeScript workflow

The Node workflow supports npm repositories with `package.json` and a committed
`package-lock.json`.

```yaml
jobs:
  node_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/node-pr-pipeline.yml@<commit-sha>
    with:
      node-version: "20"
      lint-script: "lint"
      typecheck-script: "typecheck"
      build-script: "build"
      test-script: "test"
```

Use a tested immutable commit SHA until a supported release reference is
published separately.

See [docs/NODE_PIPELINE.md](docs/NODE_PIPELINE.md) for the complete input,
security, monorepo, and migration contract.
