#!/usr/bin/env bash
# Move a moving major compatibility tag onto an already-reviewed main commit.
# Does not push. Prints the exact push command. Records PREV before moving.
#
# This is the single authorized writer for every moving compatibility tag
# (.l9/release-plane.yaml -> release_writers). Two namespaces share it because
# they share one lifecycle: force-move a mutable pointer at reviewed mainline
# code. Splitting that into a second script would be duplicate ownership of
# the same act, which the release-writer invariant exists to prevent.
#
#   v1  Core self-reference compatibility tag. Every Core workflow resolves
#       Core's own composite actions and kernel workflows through it.
#   v2  Consumer toolchain installer (install-consumer-ci@v2) and the bounded
#       optional integration channel.
#
# Neither is a Core release identity: docs/release/tag-and-release.sh owns the
# immutable vMAJOR.MINOR.PATCH namespace and never moves an alias.
#
#   tools/publish_consumer_ci_tag.sh [v1|v2] [target-committish]
#
# Target defaults to HEAD and MUST be an ancestor of origin/main. Advancing a
# compatibility tag to an unmerged feature head would let a pull request hand
# itself the Core revision it is still asking to be reviewed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

die() {
  echo "publish_consumer_ci_tag: $*" >&2
  exit 2
}

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "not a git repo"
fi

TAG="${1:-v2}"
TARGET_REF="${2:-HEAD}"

case "${TAG}" in
  v1 | v2) ;;
  *) die "'${TAG}' is not a moving compatibility tag (expected v1 or v2)" ;;
esac

TARGET="$(git rev-parse --verify "${TARGET_REF}^{commit}" 2>/dev/null)" ||
  die "cannot resolve '${TARGET_REF}' to a commit"

# --- the target must be reviewed mainline code -------------------------------
BASE="refs/remotes/origin/main"
if ! git show-ref --verify --quiet "${BASE}"; then
  die "${BASE} is missing; fetch origin before moving a compatibility tag"
fi
if ! git merge-base --is-ancestor "${TARGET}" "${BASE}"; then
  die "${TARGET} is not an ancestor of origin/main.
  A moving compatibility tag points at reviewed mainline code. Merge the
  change first; do not move the tag to make a pull request green."
fi

# --- v1 additionally carries Core's own action surface -----------------------
# Core workflows resolve these through the tag. A target missing any of them
# fails Core CI at setup with an unresolvable action reference -- the observed
# v1 bootstrap defect this check exists to prevent recurring.
if [ "${TAG}" = "v1" ]; then
  MISSING=""
  for action in \
    resolve-consumer-metadata \
    resolve-governance \
    provision-sdk \
    invoke-sdk \
    validate-bundle \
    route-artifacts \
    build-artifact-manifest; do
    if ! git cat-file -e "${TARGET}:.github/actions/${action}/action.yml" 2>/dev/null; then
      MISSING="${MISSING} ${action}"
    fi
  done
  if [ -n "${MISSING}" ]; then
    die "${TARGET} is not a compatible v1 target; missing Core actions:${MISSING}"
  fi
  echo "v1 compatibility: all seven Core self-reference actions present"
fi

PREV="none"
if git show-ref --verify --quiet "refs/tags/${TAG}"; then
  PREV="$(git rev-parse "refs/tags/${TAG}")"
fi

echo "previous ${TAG}: ${PREV}"
echo "new ${TAG}:      ${TARGET}"
echo "record previous SHA in the Core PR body before a remote retag."

# Literal per-namespace mutation sites. tools/check_release_writers.py resolves
# a mutated ref to its namespace statically; an indirected "${TAG}" would be an
# undeterminable writer, which that validator fails closed on by design.
case "${TAG}" in
  v1) git tag -f v1 "${TARGET}" ;;
  v2) git tag -f v2 "${TARGET}" ;;
esac

echo "local tag ${TAG} now at ${TARGET}"
echo "to publish: git push origin ${TAG} --force"
echo "rollback:   git tag -f ${TAG} ${PREV} && git push origin ${TAG} --force"
