# l9-ci-core

Reusable GitHub Actions workflows for L9 repositories.

Primary workflow:

```yaml
jobs:
  pr_pipeline:
    uses: Quantum-L9/l9-ci-core/.github/workflows/pr-pipeline.yml@v1
    with:
      python-version: "3.12"
      source-dir: "."
      test-dir: "tests/"
```

The workflow installs the SDK through the `l9-ci-install-command` input, which defaults to `pip install l9-ci`. Override that input until your package publishing path is live.
