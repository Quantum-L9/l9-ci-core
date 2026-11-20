#!/usr/bin/env python3
"""Refuse a Core self-pin in a required-path workflow that ``main`` does not contain.

``org-ci.yml`` and the workflows on Core's own required path reference Core's
composite actions and reusable workflows by full commit SHA
(``uses: Quantum-L9/l9-ci-core/.github/<actions|workflows>/<name>@<sha>``,
AGENTS.md §8 and §16). A pin is safe only when it names a commit that is an
ancestor of ``main``. A squash merge orphans the commits of the branch it
merged; a pin to one of them keeps working only while that branch's objects
survive, and then the org-wide required check breaks for every repository
(found 2026-11-20: ``run-repository-verification@043f50a`` from the #280
branch).

Usage (needs the commit graph of ``main``, not its trees):

    git fetch --filter=tree:0 --no-tags origin main
    python3 tools/check_core_pin_ancestry.py --main FETCH_HEAD

Exit codes: ``0`` every pin is an ancestor of ``--main``; ``1`` a pin is not,
is not a full SHA, or names a commit this clone does not have (fetch the
history; an unknown commit is never assumed safe); ``2`` invalid invocation.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# The workflows whose Core self-pins run on a required path: org-ci.yml is the
# organization's required workflow (check `Analyze (central Core)` on every
# governed repository); analyze-semgrep.yml and publish-analysis.yml run Core's
# own required `Analyze (semgrep)` check through self-analysis.yml; the
# publish and normalize reusable workflows are what that path hands off to.
REQUIRED_WORKFLOWS = (
    "org-ci.yml",
    "analyze-semgrep.yml",
    "publish-analysis.yml",
    "publish-sarif.yml",
    "normalize-semgrep-report.yml",
)

# Any Core self-reference, whatever follows the `@`, so a floating or
# malformed reference is seen and refused rather than skipped.
SELF_PIN = re.compile(
    r"uses:\s*Quantum-L9/l9-ci-core/\.github/(?:actions|workflows)/"
    r"[A-Za-z0-9._/-]+@(?P<ref>[^\s#]+)"
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

# (sha, main) -> 0 ancestor, 1 not an ancestor, anything else unknown commit.
Ancestry = Callable[[str, str], int]


def self_pins(text: str) -> list[tuple[int, str]]:
    """Every Core self-pin in ``text`` as (line number, ref)."""
    return [
        (number, match.group("ref"))
        for number, line in enumerate(text.splitlines(), start=1)
        if (match := SELF_PIN.search(line))
    ]


def git_ancestry(repository: Path) -> Ancestry:
    def check(sha: str, main: str) -> int:
        completed = subprocess.run(
            ["git", "-C", str(repository), "merge-base", "--is-ancestor", sha, main],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode

    return check


def problems(workflows: Sequence[Path], main: str, ancestry: Ancestry) -> list[str]:
    """One message per pin that is not a proven ancestor of ``main``."""
    found: list[str] = []
    verdicts: dict[str, int] = {}
    for workflow in workflows:
        try:
            text = workflow.read_text(encoding="utf-8")
        except OSError as error:
            found.append(f"{workflow.name}: unreadable ({error})")
            continue
        for number, ref in self_pins(text):
            where = f"{workflow.name}:{number}"
            if FULL_SHA.fullmatch(ref) is None:
                found.append(f"{where}: {ref!r} is not a full lowercase commit SHA")
                continue
            if ref not in verdicts:
                verdicts[ref] = ancestry(ref, main)
            verdict = verdicts[ref]
            if verdict == 1:
                found.append(f"{where}: {ref} is not an ancestor of {main}")
            elif verdict != 0:
                found.append(
                    f"{where}: {ref} is not a commit in this clone; fetch the "
                    "history of main and re-run (never assumed safe)"
                )
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_core_pin_ancestry")
    parser.add_argument(
        "--main",
        required=True,
        help="the ref whose history every pin must belong to (e.g. FETCH_HEAD)",
    )
    parser.add_argument(
        "--repository", type=Path, default=ROOT, help="git repository to query"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.main or args.main.startswith("-"):
        print("--main must name a ref", file=sys.stderr)
        return 2
    workflows = [WORKFLOWS / name for name in REQUIRED_WORKFLOWS]
    found = problems(workflows, args.main, git_ancestry(args.repository))
    for message in found:
        print(f"::error::{message}")
    if found:
        return 1
    print(
        f"Core self-pins in {len(workflows)} required-path workflow(s) are "
        f"ancestors of {args.main}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
