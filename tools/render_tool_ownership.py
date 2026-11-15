"""Render or check Core's tool-ownership manifest (``l9.tool-ownership/v1``).

    python3 tools/render_tool_ownership.py           # write the manifest
    python3 tools/render_tool_ownership.py --check   # fail when it is stale

The manifest is derived from ``toolchain-lock.json``, ``core-owned-tools.json``
and ``tools/l9_repo`` (``AGENTS.md`` §26); never edit it by hand.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from l9_repo.contract import ContractError  # noqa: E402
from l9_repo.tool_ownership import MANIFEST_PATH, render_manifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if stale")
    arguments = parser.parse_args(argv)
    try:
        rendered = render_manifest()
    except ContractError as error:
        print(f"tool-ownership: {error}", file=sys.stderr)
        return 2
    current = MANIFEST_PATH.read_bytes() if MANIFEST_PATH.is_file() else None
    if arguments.check:
        if current != rendered:
            print(
                "tool-ownership: manifest is stale; run "
                "python3 tools/render_tool_ownership.py",
                file=sys.stderr,
            )
            return 1
        print("tool-ownership: current")
        return 0
    if current != rendered:
        MANIFEST_PATH.write_bytes(rendered)
        print(f"tool-ownership: wrote {MANIFEST_PATH.name}")
    else:
        print("tool-ownership: already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
