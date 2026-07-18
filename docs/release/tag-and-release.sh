#!/usr/bin/env bash
# Tag and release l9-ci-core.
#
#   Usage: bash docs/release/tag-and-release.sh [COMMIT]
#
# COMMIT defaults to origin/main. Creates the immutable release tag v2.0.0 and
# refreshes the moving major alias v2, then (if gh is available) publishes the
# GitHub Release. Run this from a clone with permission to push tags.
set -euo pipefail

RELEASE_TAG="v2.0.0"
ALIAS_TAG="v2"
REPO="Quantum-L9/l9-ci-core"
NOTES="docs/release/RELEASE_NOTES_v2.0.0.md"

say() { printf '\n\033[1m$ %s\033[0m\n' "$*"; }
run() { say "$*"; "$@"; }

run git fetch origin --tags

TARGET="${1:-$(git rev-parse origin/main)}"
say "Releasing commit: ${TARGET}"

# Guard: refuse to move an existing immutable release tag to a new commit.
if git rev-parse -q --verify "refs/tags/${RELEASE_TAG}" >/dev/null; then
  existing="$(git rev-list -n1 "${RELEASE_TAG}")"
  if [ "${existing}" != "${TARGET}" ]; then
    echo "ERROR: ${RELEASE_TAG} already exists at ${existing} (immutable)." >&2
    echo "Cut a new version instead of moving ${RELEASE_TAG}." >&2
    exit 1
  fi
  echo "${RELEASE_TAG} already at ${TARGET}; skipping create."
else
  run git tag -a "${RELEASE_TAG}" "${TARGET}" \
    -m "l9-ci-core ${RELEASE_TAG} — thin control-plane architecture"
  run git push origin "${RELEASE_TAG}"
fi

# Moving major alias — safe to force.
run git tag -f "${ALIAS_TAG}" "${TARGET}"
run git push -f origin "${ALIAS_TAG}"

# GitHub Release (optional; needs gh authenticated).
if command -v gh >/dev/null 2>&1; then
  if gh release view "${RELEASE_TAG}" --repo "${REPO}" >/dev/null 2>&1; then
    echo "GitHub Release ${RELEASE_TAG} already exists; leaving it as-is."
  else
    run gh release create "${RELEASE_TAG}" \
      --repo "${REPO}" \
      --title "l9-ci-core ${RELEASE_TAG}" \
      --notes-file "${NOTES}"
  fi
else
  echo
  echo "gh not found — create the Release in the UI:"
  echo "  Releases -> Draft a new release -> tag ${RELEASE_TAG} -> paste ${NOTES}"
fi

echo
echo "Done. Pushing ${RELEASE_TAG} triggers .github/workflows/release-validation.yml."
