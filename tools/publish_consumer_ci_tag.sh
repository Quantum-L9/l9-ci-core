#!/usr/bin/env bash
# Point the floating v2 tag at HEAD after a merged pin-file change.
# Does not push. Prints the exact push command. Record PREV_SHA before moving.
#
# v2 is the consumer toolchain installer tag (install-consumer-ci@v2) only.
# It is not a Core release alias and not the organization CI runtime channel;
# docs/release/tag-and-release.sh never moves it (.l9/release-plane.yaml).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "publish_consumer_ci_tag: not a git repo" >&2
  exit 2
fi

HEAD="$(git rev-parse HEAD)"
PREV="none"
if git show-ref --verify --quiet refs/tags/v2; then
  PREV="$(git rev-parse refs/tags/v2)"
fi

echo "previous v2: ${PREV}"
echo "new v2:      ${HEAD}"
echo "record previous SHA in the Core PR body before a remote retag."

git tag -f v2 "${HEAD}"
echo "local tag v2 now at ${HEAD}"
echo "to publish: git push origin v2 --force"
echo "rollback:   git tag -f v2 ${PREV} && git push origin v2 --force"
