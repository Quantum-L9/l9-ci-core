#!/usr/bin/env python3
"""Signal-law conformance for Core's workflow and action ``run:`` blocks.

Quantum-L9/.github L9-SIGNAL-001: a consequential signal crossing a boundary
carries its producer, the pertinent evidence and the material context its
consumer needs. For a CI failure the consumer is a remediation agent, so a
failing exit must first name its subject in an ``::error`` annotation (the
failing command and output, the rule and file:line, the test, or the paths),
never only a status, a count or an exit code.

The check is syntactic and deliberately simple: in each ``run:`` block of
``.github/workflows/*.yml`` and ``.github/actions/*/action.yml``, every line
that exits non-zero (``exit 1``, ``exit "${code}"``, ``sys.exit(...)`` other
than ``sys.exit(0)``, ``raise SystemExit(...)``) must have an ``::error``
on that line or within the ``WINDOW`` lines before it. A site that does not
is a violation unless ``SIGNAL_LAW_EXEMPTIONS`` lists it, by file and the
exact stripped line text, with a reason. ``python3 tools/signal_law.py``
prints the violations; the test fails on any that is not exempt and on any
exemption that no longer matches a violation (so the list only shrinks).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXEMPTIONS = Path("tests/workflows/signal-law-exemptions.json")
WINDOW = 8
# ``exit`` only in command position (line start, after ; && || then else do
# or a case arm), so text such as "(exit ${code})" inside a message is not one.
FAILING_EXIT = re.compile(
    r"(?:^|[;&|]|\bthen|\belse|\bdo|\))\s*exit\s+(?!0\b)[\"']?"
    r"(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[1-9][0-9]*)"
    r"|\bsys\.exit\((?!0\)|\s*\))"
    r"|\braise\s+SystemExit\((?!0\))"
)
ERROR = "::error"
# The promotion canary embeds origin/main's org-ci.yml analyze steps verbatim
# as its base leg. That copy is judged where it comes from (org-ci.yml at
# main) and changes with every render, so it is not scanned here.
BASE_LEG = re.compile(
    r"^ {2}# --- base pins leg: begin .*?^ {2}# --- base pins leg: end ---\n",
    re.MULTILINE | re.DOTALL,
)


def _run_blocks(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from _run_blocks(value)
    elif isinstance(node, list):
        for item in node:
            yield from _run_blocks(item)


def sources(root: Path = ROOT) -> list[Path]:
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    actions = sorted((root / ".github" / "actions").glob("*/action.yml"))
    return [*workflows, *actions]


def violations(path: Path, root: Path = ROOT) -> list[tuple[str, str]]:
    """(file, stripped line) of each failing exit that names no subject."""
    document = yaml.safe_load(BASE_LEG.sub("", path.read_text(encoding="utf-8")))
    relative = path.relative_to(root).as_posix()
    found = []
    for block in _run_blocks(document):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or not FAILING_EXIT.search(line):
                continue
            window = lines[max(0, index - WINDOW) : index + 1]
            if any(ERROR in candidate for candidate in window):
                continue
            found.append((relative, stripped))
    return found


def all_violations(root: Path = ROOT) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for path in sources(root):
        counts.update(violations(path, root))
    return counts


def load_exemptions(root: Path = ROOT) -> dict[tuple[str, str], dict[str, Any]]:
    data = json.loads((root / EXEMPTIONS).read_text(encoding="utf-8"))
    exemptions = {}
    for entry in data["exemptions"]:
        if not str(entry.get("reason", "")).strip():
            raise ValueError(f"exemption without a reason: {entry}")
        exemptions[(entry["file"], entry["line"])] = entry
    return exemptions


#: Rendered files (tools/promotion_canary.py, tools/nightly_sink.py) change
#: with every render of org-ci.yml, so an exemption there may outlive its site
#: without being wrong; the source files are judged for staleness.
GENERATED = frozenset(
    {".github/workflows/promotion-canary.yml", ".github/workflows/nightly-sink.yml"}
)


def stale_exemptions(
    counts: Counter[tuple[str, str]], exempt: dict[tuple[str, str], dict[str, Any]]
) -> list[tuple[str, str]]:
    return sorted(
        key for key in exempt if key not in counts and key[0] not in GENERATED
    )


def main() -> int:
    counts = all_violations()
    exempt = load_exemptions()
    open_sites = {key: n for key, n in counts.items() if key not in exempt}
    for (file, line), n in sorted(open_sites.items()):
        print(
            f"::error file={file},title=Signal law::failing exit names no subject ({n}x): {line}"
        )
    stale = stale_exemptions(counts, exempt)
    for file, line in stale:
        print(
            f"::error file={file},title=Signal law::exemption no longer matches a site: {line}"
        )
    print(
        f"signal law: {sum(counts.values())} failing exit(s) without ::error, "
        f"{len(exempt)} exempt site(s), {len(open_sites)} open, {len(stale)} stale"
    )
    return 1 if open_sites or stale else 0


if __name__ == "__main__":
    sys.exit(main())
