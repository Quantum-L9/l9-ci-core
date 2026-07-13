# SDK Install Path

`l9-ci-core`'s reusable workflows consume the `l9-ci` SDK as a **runtime CLI**.
Each job that needs the SDK installs it via the `l9-ci-install-command` input and
then invokes the `l9-ci` command directly. The SDK is not vendored, is not a
checked-in dependency, and core tests do not import `l9_ci`.

## Public default (TEMPORARY)

Every workflow exposes an `l9-ci-install-command` input. Its default is:

```
python -m pip install "l9-ci @ git+https://github.com/Quantum-L9/l9-ci-sdk.git@d32e84b7c00fc88b85f2639471dd64126251e09e"
```

This installs the SDK straight from the SDK repository, pinned to the immutable
commit `d32e84b` via `git+https`. For a public SDK repo, no token is required.

> **TEMPORARY:** This git+https default is a stopgap until the SDK is published to
> an index (a `v1.0.0` tag / PyPI release). It is intentionally pinned to a
> commit SHA rather than a mutable tag so the install is reproducible. The
> `v0.1.0` tag was deliberately **not** used because it predates the merged SDK
> remediations, so pinning to it would ship stale behaviour.

## Private path (`SDK_TOKEN`)

If the SDK repository is private, the caller passes an `SDK_TOKEN` secret with
read access to `Quantum-L9/l9-ci-sdk`. Each installing job runs a
`Configure private SDK access` step immediately before the install step. That
step is **step-scoped**: the secret is exposed via `env:` only on that step, not
at the workflow or job level.

```yaml
- name: Configure private SDK access
  env:
    SDK_TOKEN: ${{ secrets.SDK_TOKEN }}
  run: |
    if [ -n "${SDK_TOKEN:-}" ]; then
      git config --global url."https://x-access-token:${SDK_TOKEN}@github.com/".insteadOf "https://github.com/"
    fi
```

When `SDK_TOKEN` is set, the step rewrites the git transport with
`git config insteadOf` so the pinned `git+https` install authenticates. The
token is read from the environment and **never echoed**; the step does not use
`set -x` and never prints the tokenized URL. When `SDK_TOKEN` is empty (the
public case), the step is a no-op.

## Caller usage

```yaml
jobs:
  pr:
    uses: Quantum-L9/l9-ci-core/.github/workflows/pr-pipeline.yml@v1
    secrets:
      SDK_TOKEN: ${{ secrets.SDK_TOKEN }}   # only needed if the SDK repo is private
```

Public callers omit the `secrets:` block entirely; the secret is declared
`required: false` on every workflow.

## Override example

`l9-ci-install-command` remains fully caller-overridable. To install a different
ref (branch, tag, or another commit):

```yaml
jobs:
  pr:
    uses: Quantum-L9/l9-ci-core/.github/workflows/pr-pipeline.yml@v1
    with:
      l9-ci-install-command: >-
        python -m pip install
        "l9-ci @ git+https://github.com/Quantum-L9/l9-ci-sdk.git@main"
```

## Fail-closed behaviour

The install command runs directly (no `|| true`, no failure masking). If the SDK
install fails, the job fails. The tool-ensure lines elsewhere in the jobs
(`command -v X || pip install X`) only backfill unrelated CI tooling and do not
mask the SDK install.

## When to switch back to `pip install l9-ci`

Once the SDK is published to an index and a stable `v1.0.0` (or later) release
exists, change every `l9-ci-install-command` default back to
`pip install l9-ci`, drop this temporary git+https pin, and update the README.
The `SDK_TOKEN` plumbing can remain harmlessly (no-op when unset) or be removed
if the published package is public.
