#!/usr/bin/env bash
# Cut an immutable l9-ci-core release.
#
#   Usage: bash docs/release/tag-and-release.sh X.Y.Z [COMMIT]
#
# Runs the release validator against COMMIT (default origin/main) as a
# preflight, then creates the annotated immutable tag vX.Y.Z, pushes it, and
# (if gh is available) creates the GitHub Release from
# docs/release/RELEASE_NOTES_vX.Y.Z.md. The preflight runs the full unittest
# suite, so python3 with PyYAML must be available where this script runs.
#
# Releases are audit anchors (.l9/release-plane.yaml). The organization CI
# runtime is Core main, bound directly by the GitHub organization ruleset, so
# this script moves NO major alias: refs/tags/v2 is not a release ref and is
# never touched here. Run from a clone with permission to push tags.
set -euo pipefail

REPO="Quantum-L9/l9-ci-core"

say() { printf '\n\033[1m$ %s\033[0m\n' "$*"; }
run() { say "$*"; "$@"; }
die() { echo "ERROR: $*" >&2; exit 1; }

VERSION="${1:-}"
[ -n "${VERSION}" ] || die "usage: $0 X.Y.Z [COMMIT]"
VERSION="${VERSION#v}"
if ! [[ "${VERSION}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  die "'${VERSION}' is not an exact MAJOR.MINOR.PATCH version (aliases are not releases)"
fi
RELEASE_TAG="v${VERSION}"
NOTES="docs/release/RELEASE_NOTES_${RELEASE_TAG}.md"

run git fetch origin --tags

TARGET="${2:-$(git rev-parse origin/main)}"
TARGET="$(git rev-parse --verify "${TARGET}^{commit}")"
say "Releasing ${RELEASE_TAG} at commit: ${TARGET}"

# Guard: the released tree must declare this exact version.
declared="$(git show "${TARGET}:.l9/repo-spec.yaml" \
  | sed -n -E 's/^[[:space:]]+version:[[:space:]]*["'"'"']?([^"'"'"'[:space:]]+).*/\1/p' \
  | head -n1)"
[ "${declared}" = "${VERSION}" ] \
  || die ".l9/repo-spec.yaml at ${TARGET} declares version '${declared}', not '${VERSION}'"

# Guard: release notes must exist in the released tree.
git cat-file -e "${TARGET}:${NOTES}" 2>/dev/null \
  || die "${NOTES} is missing from ${TARGET}; add the notes before releasing"

# Preflight: run the release validator against the exact target commit BEFORE
# the immutable tag exists. An invalid tag cannot be moved, only superseded,
# so the check runs first. The commit is checked out into a temporary
# detached worktree (a real git checkout: the validation suite enumerates
# tracked files), so the operator's working tree, which may differ from
# TARGET, is never validated by mistake. This is the same validator
# release-validation.yml runs after the tag; that later run is the
# independent attestation, not the first check.
PREFLIGHT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/l9-core-release-preflight.XXXXXX")"
rmdir "${PREFLIGHT_DIR}"
cleanup_preflight() {
  git worktree remove --force "${PREFLIGHT_DIR}" >/dev/null 2>&1 || true
  git worktree prune >/dev/null 2>&1 || true
}
trap cleanup_preflight EXIT
say "Preflight: checking out ${TARGET} into ${PREFLIGHT_DIR}"
git worktree add --detach "${PREFLIGHT_DIR}" "${TARGET}" >/dev/null
say "Preflight: validate_release.py (tag ${RELEASE_TAG})"
if ! GITHUB_WORKSPACE="${PREFLIGHT_DIR}" \
     L9_RELEASE_TAG="${RELEASE_TAG}" \
     L9_EXPECTED_VERSION="" \
     python3 "${PREFLIGHT_DIR}/.github/actions/validate-release/validate_release.py"; then
  die "release preflight failed for ${TARGET}; no tag was created. Fix main and re-run."
fi
say "Preflight passed for ${RELEASE_TAG} at ${TARGET}"

# Guard: refuse to move an existing immutable release tag.
if git rev-parse -q --verify "refs/tags/${RELEASE_TAG}" >/dev/null; then
  existing="$(git rev-list -n1 "${RELEASE_TAG}")"
  [ "${existing}" = "${TARGET}" ] \
    || die "${RELEASE_TAG} already exists at ${existing} (immutable). Cut a new version instead."
  echo "${RELEASE_TAG} already at ${TARGET}; skipping create."
else
  run git tag -a "${RELEASE_TAG}" "${TARGET}" -m "l9-ci-core ${RELEASE_TAG}"
  run git push origin "${RELEASE_TAG}"
fi

# GitHub Release (optional; needs gh authenticated).
if command -v gh >/dev/null 2>&1; then
  if gh release view "${RELEASE_TAG}" --repo "${REPO}" >/dev/null 2>&1; then
    echo "GitHub Release ${RELEASE_TAG} already exists; leaving it as-is."
  else
    run gh release create "${RELEASE_TAG}" \
      --repo "${REPO}" \
      --title "l9-ci-core ${RELEASE_TAG}" \
      --notes-file "${NOTES}" \
      --verify-tag
  fi
else
  echo
  echo "gh not found — create the Release in the UI:"
  echo "  Releases -> Draft a new release -> tag ${RELEASE_TAG} -> paste ${NOTES}"
fi

echo
echo "Done. Pushing ${RELEASE_TAG} triggers .github/workflows/release-validation.yml."
echo "No alias was moved: main is the runtime channel; ${RELEASE_TAG} is an audit anchor."
