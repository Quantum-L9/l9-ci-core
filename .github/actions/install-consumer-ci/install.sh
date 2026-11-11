#!/usr/bin/env bash
# Install the Core-owned consumer CI pins (pip, pre-commit, ruff, mypy, pytest).
# Fail-closed on missing or unpinned lines.
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

# Every line is an exact pin: a consumer declares none of these tools, so this
# file is the only place their versions come from.
while IFS= read -r line || [[ -n "${line}" ]]; do
  case "${line}" in
    ""|\#*|-r\ *) continue ;;
  esac
  if [[ ! "${line}" =~ ^[A-Za-z0-9._-]+==[0-9][^[:space:]]*$ ]]; then
    echo "install-consumer-ci: unpinned toolchain line refused: ${line}" >&2
    exit 2
  fi
done < "${PIN}"

for pkg in pip pre-commit ruff mypy pytest; do
  if ! grep -Eq "^${pkg}==" "${PIN}"; then
    echo "install-consumer-ci: missing ${pkg}== pin in ${PIN}" >&2
    exit 2
  fi
done

# --only-binary :all: refuses source distributions. Installing a dependency must
# not be able to execute it: an sdist runs its setup.py at install time, on
# every consumer runner that calls this action. ruff, mypy and pytest all
# publish wheels, so this costs nothing and fails loudly rather than silently
# building if that stops being true. pip and pre-commit publish wheels too.
#
# This is also what SonarCloud githubactions:S8541 asks for by name, and this
# action is the fleet's shared install path -- leaving it unhardened re-seeds
# that finding into every consumer that adopts the preset.
python -m pip install --only-binary :all: -r "${PIN}"

# Semgrep, on by default (the action input defaults to "true"; "false" skips
# it; a bare script run without the variable installs it too). The consumer
# names no version: it is
# toolchain-lock.json's `semgrep` from this same Core revision, installed by
# install-semgrep's hash-locked contract (every distribution digest-pinned,
# wheels only), which also verifies the installed version and writes the
# executable path to GITHUB_OUTPUT.
case "${L9_INSTALL_SEMGREP:-true}" in
  false) exit 0 ;;
  true|"") ;;
  *)
    echo "install-consumer-ci: semgrep must be \"true\" or \"false\", got '${L9_INSTALL_SEMGREP}'" >&2
    exit 2
    ;;
esac
LOCK_JSON="${HERE}/toolchain-lock.json"
SEMGREP_INSTALLER="${HERE}/../install-semgrep/install.sh"
if [[ ! -f "${LOCK_JSON}" || ! -f "${SEMGREP_INSTALLER}" ]]; then
  echo "install-consumer-ci: semgrep needs this action's Core revision (toolchain-lock.json and install-semgrep beside it)" >&2
  exit 2
fi
SEMGREP_VERSION="$(python -c 'import json, sys; print(json.load(open(sys.argv[1]))["semgrep"])' "${LOCK_JSON}")"
L9_SEMGREP_VERSION="${SEMGREP_VERSION}" bash "${SEMGREP_INSTALLER}"
