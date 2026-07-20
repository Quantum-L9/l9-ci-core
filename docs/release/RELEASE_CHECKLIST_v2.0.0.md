# Release runbook — l9-ci-core v2.0.0

Single-file checklist to cut the immutable **v2.0.0** release. Automated Claude
sessions cannot push tags (the git gateway denies `refs/tags/*` with 403), so
this must be run once by a human with push rights. Everything the release needs
already exists in the repo — this is the trigger, not new logic.

## Preconditions

- [ ] This PR is merged to `main`.
- [ ] `main` HEAD is the commit you intend to release (contains the v2 rewrite,
      the `.l9/` contracts with `version: 2.0.0` + phase 4, and SHA-pinned
      actions). Any current `main` commit qualifies.
- [ ] You have push access to `Quantum-L9/l9-ci-core` and `gh` is authenticated.

## Cut the release (one command)

```bash
git fetch origin
git checkout main && git pull --ff-only
bash docs/release/tag-and-release.sh        # tags origin/main HEAD
```

`tag-and-release.sh` (already on `main`) will:

1. Create the annotated immutable tag **`v2.0.0`** and push it — refusing to
   move it if it already exists (immutable-safe).
2. Force-update the moving alias **`v2`** to the same commit.
3. Create the GitHub Release from `docs/release/RELEASE_NOTES_v2.0.0.md`
   (or print UI steps if `gh` is absent).

Pushing `v2.0.0` automatically triggers `.github/workflows/release-validation.yml`.

## Acceptance criteria

- [ ] Tag `v2.0.0` exists and points at the released `main` commit.
- [ ] Moving alias `v2` points at the same commit.
- [ ] **`Phase 4 release validation`** is green for the `v2.0.0` tag
      (semver match to `version: 2.0.0`, `.l9` contracts present, all external
      actions full-SHA pinned, full test suite passes).
- [ ] GitHub Release `v2.0.0` is published with the notes.
- [ ] Consumers can pin `@v2.0.0` (immutable) or `@v2` (moving alias).

## If validation fails

Delete the tag, fix the flagged item on `main`, and re-run:

```bash
git push origin :refs/tags/v2.0.0 && git tag -d v2.0.0
# fix, merge to main, then re-run tag-and-release.sh
```

Do **not** move `v2.0.0` to a different commit once consumers depend on it — cut
`v2.0.1` instead.
