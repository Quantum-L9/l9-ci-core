# Consumer lint + test workflow

The v1 `pr-pipeline.yml@v1` reusable workflow bundled two unrelated things:

1. **The L9 analysis pipeline** — semgrep normalization, canonical bundles,
   gate publication.
2. **Generic Python CI hygiene** — `ruff`, `ruff format`, `mypy`, `pytest` +
   coverage.

l9-ci-core **v2** keeps (1) — as governed, phase-scoped reusable workflows and
composite actions (`profile-normalize-semgrep.yml`, `publish-analysis.yml`, the
`.github/actions/*` set). It intentionally **drops (2) from Core**.

## Why this lives in your repo, not Core

v2 is a *thin control plane*. Its workflow set is frozen by
`tests/workflows/test_phase_scope.py`, which asserts the exact seven Phase 1–4
workflows and nothing else. Generic lint/test is not part of Core's phase
model and is not SDK-owned behavior — it is ordinary repository CI that each
consumer owns. Hosting it inside Core would weaken the boundary that defines
the rewrite.

So the replacement ships as a **template you copy**, not a workflow you call.

## Adopt it

1. Copy [`templates/l9-lint-test.yml`](./templates/l9-lint-test.yml) into your
   repository at `.github/workflows/l9-lint-test.yml`.
2. Edit the `env:` block at the top:

   | Variable | Meaning | Default |
   |---|---|---|
   | `PYTHON_VERSION` | Interpreter version | `3.12` |
   | `SOURCE_DIR` | Path passed to `mypy` / `--cov` | `.` |
   | `TEST_DIR` | pytest target directory | `tests/` |
   | `COVERAGE_THRESHOLD` | `--cov-fail-under` percentage; `0` = advisory | `0` |

3. Commit. It runs on `pull_request`, `push` to `main`, and manual dispatch.

## What it preserves from v2 style

- **Immutable event-revision checkout** — raw `git fetch`/`checkout FETCH_HEAD`
  of `github.sha`, matching `self-ci.yml`; no floating action ref for source.
- **SHA-pinned external actions** — `actions/setup-python` is pinned to a full
  40-char commit SHA (the same pin the platform already vets).
- **Least privilege** — `contents: read` only; no `write` scopes, no PR
  comments, no token-bearing uploads.

## Pairing with the analysis pipeline

Run this alongside the governed semgrep pipeline. Pin Core by the immutable
release commit (or the `v2` tag once published):

```yaml
jobs:
  semgrep:
    uses: Quantum-L9/l9-ci-core/.github/workflows/profile-normalize-semgrep.yml@54a2f2fc8d060674d544fab14388bb5eff6b8e78
    with:
      profile: pr_fast
      report-path: artifacts/raw/semgrep/pr/report.json
      snapshot-id: ${{ github.sha }}
      matrix-id: pr-semgrep
    permissions:
      contents: read
      checks: write
```

## Not covered here

`mypy`/`ruff`/`pytest` are generic dev tools, not the L9 finding pipeline. This
template performs no canonical-finding construction, no gate computation, and
no SDK invocation — those remain owned by the SDK and the Core analysis
workflows.
