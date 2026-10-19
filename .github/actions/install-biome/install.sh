#!/usr/bin/env bash
# Install the centrally selected Biome binary from a digest-locked contract.
#
# Biome ships a single native binary per platform. Core selects the version
# (`toolchain-lock.json` key `biome`, shipped in the same pinned Core revision)
# and the bytes (`locks/biome-<version>.json`, platform -> SHA-256). A version
# without a lock, a platform without a digest, or a download whose digest does
# not match fails closed; nothing unverified is ever made executable.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
CORE_LOCK="${HERE}/../install-consumer-ci/toolchain-lock.json"
VERSION="${L9_BIOME_VERSION:-}"

if [[ -z "${VERSION}" ]]; then
  # The lock travels with this action: GitHub materialises the whole pinned
  # Core revision for a `uses:` edge, so the sibling is the same commit's
  # file, never a consumer file and never another revision.
  if [[ -L "${CORE_LOCK}" || ! -f "${CORE_LOCK}" ]]; then
    echo "install-biome: Core toolchain lock missing beside the action (${CORE_LOCK})" >&2
    exit 2
  fi
  VERSION="$(python3 -I -c '
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8")).get("biome")
print(value if isinstance(value, str) else "")
' "${CORE_LOCK}")"
fi

if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "install-biome: biome version must be an exact x.y.z version, got '${VERSION}'" >&2
  exit 2
fi

LOCK_NAME="locks/biome-${VERSION}.json"
LOCK="${HERE}/${LOCK_NAME}"
if [[ -L "${LOCK}" || ! -f "${LOCK}" ]]; then
  echo "install-biome: no digest lock for biome ${VERSION} (expected ${LOCK_NAME}); add the lock to Core before selecting this version" >&2
  exit 2
fi

case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) PLATFORM="linux-x64" ;;
  Linux/aarch64 | Linux/arm64) PLATFORM="linux-arm64" ;;
  *)
    echo "install-biome: unsupported runner platform $(uname -s)/$(uname -m)" >&2
    exit 2
    ;;
esac

# Read and validate the lock entry with the standard library only.
entry="$(python3 -I -c '
import json, re, sys
lock, version, platform = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(lock, encoding="utf-8"))
if data.get("schema") != "l9.biome-binary-lock/v1" or data.get("version") != version:
    raise SystemExit("lock schema/version mismatch")
item = (data.get("platforms") or {}).get(platform)
if not isinstance(item, dict):
    raise SystemExit(f"lock has no digest for platform {platform}")
asset, digest = item.get("asset"), item.get("sha256")
if not isinstance(asset, str) or not re.fullmatch(r"biome-[a-z0-9-]+", asset):
    raise SystemExit("lock asset name is invalid")
if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
    raise SystemExit("lock sha256 is invalid")
print(asset, digest)
' "${LOCK}" "${VERSION}" "${PLATFORM}")" || {
  echo "install-biome: ${LOCK_NAME} is not a valid lock for ${PLATFORM}" >&2
  exit 2
}
ASSET="${entry% *}"
DIGEST="${entry#* }"

if [[ -n "${RUNNER_TEMP:-}" ]]; then
  DEST="${RUNNER_TEMP}/l9-biome-${VERSION}"
else
  DEST="$(mktemp -d)/l9-biome-${VERSION}"
fi
mkdir -p "${DEST}"
DOWNLOAD="${DEST}/${ASSET}.download"
EXECUTABLE="${DEST}/biome"
rm -f "${DOWNLOAD}" "${EXECUTABLE}"

URL="https://github.com/biomejs/biome/releases/download/%40biomejs%2Fbiome%40${VERSION}/${ASSET}"
curl -fsSL --retry 3 -o "${DOWNLOAD}" "${URL}"

if ! printf '%s  %s\n' "${DIGEST}" "${DOWNLOAD}" | sha256sum --check --status; then
  rm -f "${DOWNLOAD}"
  echo "install-biome: ${ASSET} digest does not match ${LOCK_NAME}; refusing the binary" >&2
  exit 2
fi
mv "${DOWNLOAD}" "${EXECUTABLE}"
chmod 0755 "${EXECUTABLE}"

installed="$("${EXECUTABLE}" --version | tr -d '[:space:]')"
if [[ "${installed}" != "Version:${VERSION}" ]]; then
  echo "install-biome: verified binary reports '${installed}', expected Version:${VERSION}" >&2
  exit 2
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "executable=${EXECUTABLE}"
    echo "provider-version=${VERSION}"
    echo "binary-sha256=${DIGEST}"
  } >> "${GITHUB_OUTPUT}"
fi
echo "install-biome: biome ${VERSION} (${PLATFORM}) installed from ${LOCK_NAME} (sha256:${DIGEST})"
