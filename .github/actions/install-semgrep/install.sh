#!/usr/bin/env bash
# Install the centrally selected Semgrep from a hash-locked contract.
#
# Fail closed on an unlocked version: `semgrep==X` alone pins one package and
# lets pip resolve the rest of the closure against whatever PyPI and the runner
# image offer that day, which is how the organization-wide required gate could
# change behavior without a Core source revision changing.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
VERSION="${L9_SEMGREP_VERSION:-}"

if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "install-semgrep: semgrep-version must be an exact x.y.z version, got '${VERSION}'" >&2
  exit 2
fi

LOCK="${HERE}/locks/semgrep-${VERSION}.txt"
LOCK_NAME="locks/semgrep-${VERSION}.txt"
if [[ ! -f "${LOCK}" ]]; then
  echo "install-semgrep: no locked install contract for semgrep ${VERSION} (expected ${LOCK_NAME}); add the lock to Core before selecting this version" >&2
  exit 2
fi

escaped="${VERSION//./\\.}"
if ! grep -Eq "^semgrep==${escaped}( |$)" "${LOCK}"; then
  echo "install-semgrep: ${LOCK_NAME} does not pin semgrep==${VERSION}" >&2
  exit 2
fi

# --require-hashes: every requirement is an exact version with sha256 digests;
# pip refuses any unlisted package, version, or bytes, including a transitive
# dependency the lock does not name. --only-binary :all: refuses source
# distributions, so installing a dependency can never execute it on the runner.
python -m pip install --only-binary :all: --require-hashes -r "${LOCK}"

# Resolve the console script from the same interpreter that performed the
# hash-locked install. `command -v semgrep` is not authoritative: a stale or
# attacker-controlled PATH entry could win even though pip installed the locked
# closure successfully.
scripts_directory="$(python -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
executable="${scripts_directory}/semgrep"
if [[ "${scripts_directory}" != /* || ! -f "${executable}" || ! -x "${executable}" ]]; then
  echo "install-semgrep: locked install did not produce an executable at ${executable}" >&2
  exit 2
fi
installed="$("${executable}" --version | tr -d '[:space:]')"
if [[ "${installed}" != "${VERSION}" ]]; then
  echo "install-semgrep: installed semgrep '${installed}', expected ${VERSION}" >&2
  exit 2
fi
lock_sha256="$(sha256sum "${LOCK}" | awk '{print $1}')"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "executable=${executable}"
    echo "provider-version=${VERSION}"
    echo "lock-file=${LOCK_NAME}"
    echo "lock-sha256=${lock_sha256}"
  } >> "${GITHUB_OUTPUT}"
fi
echo "install-semgrep: semgrep ${VERSION} installed from ${LOCK_NAME} (sha256:${lock_sha256})"
