#!/usr/bin/env bash
# Install the Core-owned consumer CI pins. Fail-closed on missing or unpinned lines.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REF="${L9_CI_CORE_REF:-v2}"
PIN="${HERE}/requirements-consumer-ci.txt"

if [[ ! -f "${PIN}" ]]; then
  PIN="$(mktemp)"
  curl -fsSL \
    "https://raw.githubusercontent.com/Quantum-L9/l9-ci-core/${REF}/.github/actions/install-consumer-ci/requirements-consumer-ci.txt" \
    -o "${PIN}"
fi

while IFS= read -r line || [[ -n "${line}" ]]; do
  case "${line}" in
    ""|\#*|-r\ *) continue ;;
  esac
  pkg="${line%%==*}"
  case "${pkg}" in
    ruff|mypy|pytest)
      if [[ "${line}" != *==* ]]; then
        echo "install-consumer-ci: unpinned toolchain line refused: ${line}" >&2
        exit 2
      fi
      ;;
  esac
done < "${PIN}"

for pkg in ruff mypy pytest; do
  if ! grep -Eq "^${pkg}==" "${PIN}"; then
    echo "install-consumer-ci: missing ${pkg}== pin in ${PIN}" >&2
    exit 2
  fi
done

python -m pip install -r "${PIN}"
