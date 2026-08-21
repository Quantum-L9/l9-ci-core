#!/usr/bin/env bash
# Stamp the locked TypeScript Biome contract into a consumer repository.
#
# Agents MUST call this instead of inventing biome.json. The formatter/linter
# blocks are locked; do not rewrite them. Extra path excludes may be appended
# to files.includes after the stamp, never invented from scratch.
#
# Usage:
#   bash presets/typescript/stamp.sh <consumer-repo-root>
#
# Existing biome.json / .editorconfig are kept (never overwritten).
set -euo pipefail

PRESET="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-}"
if [[ -z "${TARGET}" || ! -d "${TARGET}" ]]; then
  echo "usage: stamp.sh <consumer-repo-root>" >&2
  exit 2
fi
TARGET="$(cd "${TARGET}" && pwd)"

stamp_if_absent() {
  local src="$1"
  local dest="$2"
  local rel="${dest#"${TARGET}"/}"
  if [[ -e "${dest}" ]]; then
    echo "keep existing ${rel}"
    return 0
  fi
  mkdir -p "$(dirname "${dest}")"
  cp "${src}" "${dest}"
  echo "stamped ${rel}"
}

stamp_if_absent "${PRESET}/biome.json" "${TARGET}/biome.json"
stamp_if_absent "${PRESET}/.biomeignore" "${TARGET}/.biomeignore"
stamp_if_absent "${PRESET}/.editorconfig" "${TARGET}/.editorconfig"

# Recommend the Biome plugin. Cursor-Governance install_ide_profile.sh owns
# .vscode/settings.json once biome.json is present (biome_default class).
python3 - "${PRESET}/.vscode/extensions.json" "${TARGET}/.vscode/extensions.json" <<'PY'
import json
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])
wanted = json.loads(src.read_text(encoding="utf-8"))
recommend = list(wanted.get("recommendations") or [])
if dest.is_file():
    current = json.loads(dest.read_text(encoding="utf-8"))
    existing = list(current.get("recommendations") or [])
    merged = list(dict.fromkeys([*existing, *recommend]))
    if merged == existing:
        print("keep existing .vscode/extensions.json")
        raise SystemExit(0)
    current["recommendations"] = merged
    dest.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print("merged biomejs.biome into .vscode/extensions.json")
else:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print("stamped .vscode/extensions.json")
PY
