#!/usr/bin/env python3
"""Advance every Core composite-action pin in the organization workflow.

``org-ci.yml`` runs from Core ``main`` but references Core's composite actions
by full commit SHA (``uses: Quantum-L9/l9-ci-core/.github/actions/<name>@<sha>``,
AGENTS.md §8). A change to an action — its policy data included — therefore
reaches the fleet only when those pins advance to a revision that carries it.
That is a two-step, by design: land the action change, then pin its merge
revision. This tool does the second step mechanically and verifiably:

    python3 tools/advance_core_action_pins.py <40-hex sha>            # rewrite
    python3 tools/advance_core_action_pins.py <40-hex sha> --check    # verify only
    python3 tools/advance_core_action_pins.py <40-hex sha> --dry-run  # show, no write

It rewrites only the SHA after ``@`` on ``uses:`` lines whose target starts
with ``Quantum-L9/l9-ci-core/.github/actions/``; comments, other actions and
every other byte are untouched. ``--check`` exits ``1`` when any Core action
pin differs from the given SHA, so a test or a reviewer can prove the fleet
runs one revision. The SHA is required to be 40 lowercase hex characters;
nothing else (a branch, a tag, ``main``) is accepted, because a floating
reference is forbidden (AGENTS.md §8).

Exit codes: ``0`` done or verified, ``1`` ``--check`` found a differing pin,
``2`` invalid invocation.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CORE_ACTION_PIN = re.compile(
    r"^(?P<prefix>\s*(?:-\s*)?uses:\s*Quantum-L9/l9-ci-core/\.github/actions/"
    r"[A-Za-z0-9._-]+@)(?P<sha>[0-9a-f]{40})(?P<suffix>\s*(?:#.*)?)$"
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_WORKFLOW = Path(".github/workflows/org-ci.yml")


def rewrite(text: str, sha: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Return the rewritten text and ``(line, old_sha, new_sha)`` per change."""

    changed: list[tuple[int, str, str]] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = CORE_ACTION_PIN.match(line)
        if match is None or match.group("sha") == sha:
            continue
        changed.append((index + 1, match.group("sha"), sha))
        lines[index] = f"{match.group('prefix')}{sha}{match.group('suffix')}"
    return "\n".join(lines), changed


def pins(text: str) -> list[tuple[int, str]]:
    """Every Core action pin in ``text`` as ``(line, sha)``."""

    return [
        (number, match.group("sha"))
        for number, line in enumerate(text.split("\n"), start=1)
        if (match := CORE_ACTION_PIN.match(line)) is not None
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("sha", help="full 40-character lowercase commit SHA")
    parser.add_argument(
        "--workflow",
        type=Path,
        default=DEFAULT_WORKFLOW,
        help=f"workflow to rewrite (default {DEFAULT_WORKFLOW})",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify, never write")
    mode.add_argument("--dry-run", action="store_true", help="show, never write")
    args = parser.parse_args(argv)

    if not FULL_SHA.fullmatch(args.sha):
        print(
            "advance-core-action-pins: sha must be 40 lowercase hex characters "
            "(a branch, tag or short revision is a floating reference)",
            file=sys.stderr,
        )
        return 2
    workflow: Path = args.workflow
    if workflow.is_symlink() or not workflow.is_file():
        print(
            f"advance-core-action-pins: no such workflow: {workflow}", file=sys.stderr
        )
        return 2
    text = workflow.read_text(encoding="utf-8")
    found = pins(text)
    if not found:
        print(
            f"advance-core-action-pins: {workflow} carries no Core action pin",
            file=sys.stderr,
        )
        return 2

    if args.check:
        drift = [(number, sha) for number, sha in found if sha != args.sha]
        for number, sha in drift:
            print(f"{workflow}:{number}: pinned {sha}, expected {args.sha}")
        if drift:
            return 1
        print(f"{workflow}: all {len(found)} Core action pin(s) at {args.sha}")
        return 0

    rewritten, changed = rewrite(text, args.sha)
    for number, old, new in changed:
        print(f"{workflow}:{number}: {old} -> {new}")
    if not changed:
        print(f"{workflow}: all {len(found)} Core action pin(s) already at {args.sha}")
        return 0
    if args.dry_run:
        print(f"dry run: {len(changed)} pin(s) would change")
        return 0
    workflow.write_text(rewritten, encoding="utf-8")
    print(f"{workflow}: advanced {len(changed)} pin(s) to {args.sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
