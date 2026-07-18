# Tagging and releasing l9-ci-core

Repo: <https://github.com/Quantum-L9/l9-ci-core>

Two tags, two purposes:

| Tag | Kind | Purpose | Validated by CI? |
|---|---|---|---|
| `v2.0.0` | **immutable release** | The pinned, auditable release of v2. | **Yes** — `.github/workflows/release-validation.yml` fires on `v*.*.*`. |
| `v2` | **moving major alias** | Convenience ref so consumers can pin `@v2` and get the latest v2.x. | No (it is not `v*.*.*` semver and is not a release). |

Consumers may pin any of: the immutable release `@v2.0.0`, the moving alias
`@v2`, or a full commit SHA. Prefer `@v2.0.0` or a SHA for reproducibility.

## What the release gate checks

When you push a `vX.Y.Z` tag, `release-validation.yml` checks out the tagged
revision and runs `validate-release` with `expected-version: 2.0.0`. It fails
closed unless **all** hold:

- Tag is valid semver and equals the expected version (`v2.0.0` ↔ `2.0.0`).
- `.l9/repo-spec.yaml` contains `version: 2.0.0` and `phase_4: … status: implemented`.
- `.l9/architecture.yaml` declares `phase: 4`, phase 4 implemented.
- `.l9/publication-contract.yaml` is the authoritative publication contract.
- Every external action under `.github/**` is pinned to a full 40-char SHA.
- The full `unittest` suite passes.

So: only tag a commit that already satisfies these (any current `main` commit
does — the `.l9/` contracts and pins are intact).

## Option A — scripted (recommended)

From a clone with push rights (this must run somewhere allowed to push tags —
the Claude session's git gateway blocks tag refs, which is why this is a script
you run, not something already pushed):

```bash
# tag current origin/main as the v2.0.0 release + refresh the v2 alias,
# then (optionally) create the GitHub Release from the notes file.
bash docs/release/tag-and-release.sh
```

Pass an explicit commit to release a specific revision:

```bash
bash docs/release/tag-and-release.sh 54a2f2fc8d060674d544fab14388bb5eff6b8e78
```

The script prints each command before running it and is safe to re-run (it
force-updates only the moving `v2` alias, never the immutable `v2.0.0`).

## Option B — manual

```bash
git fetch origin
REL=$(git rev-parse origin/main)     # or a specific SHA

# Immutable release tag (annotated)
git tag -a v2.0.0 "$REL" -m "l9-ci-core v2.0.0 — thin control-plane architecture"
git push origin v2.0.0

# Moving major alias
git tag -f v2 "$REL"
git push -f origin v2

# GitHub Release (requires gh auth)
gh release create v2.0.0 \
  --repo Quantum-L9/l9-ci-core \
  --title "l9-ci-core v2.0.0" \
  --notes-file docs/release/RELEASE_NOTES_v2.0.0.md
```

No `gh`? Create the release in the UI: **Releases → Draft a new release →**
choose tag `v2.0.0` → paste `RELEASE_NOTES_v2.0.0.md` → Publish. Pushing the
`v2.0.0` tag alone already triggers `release-validation.yml`.

## After releasing

- Confirm `release-validation.yml` is green for the `v2.0.0` tag.
- Optionally repoint the consumer templates in `docs/templates/` from the SHA
  pin to `@v2.0.0`.
- For the next release, bump `version:` in `.l9/repo-spec.yaml` and
  `expected-version` in `release-validation.yml`, then tag `vX.Y.Z`.
