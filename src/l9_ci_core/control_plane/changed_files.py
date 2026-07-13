"""Changed-file acquisition.

Runs ``git diff --name-only <base>...<subject>`` using an argument array (never
shell interpolation) and normalizes the output to sorted, de-duplicated,
repository-relative POSIX paths. A diff failure — or a missing base SHA — yields
an ``unknown_diff`` result instead of raising, so the planner can still build a
conservative fail-closed (high-risk) plan.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .models import ChangedFiles, EventContext

__all__ = ["collect", "collect_for_context"]


def _normalize(raw: str) -> list[str]:
    seen: set[str] = set()
    for line in raw.splitlines():
        path = line.strip()
        if not path:
            continue
        # Defensive: git emits repo-relative POSIX paths, but never allow an
        # absolute path or a parent-escape to slip through.
        if path.startswith("/") or path.startswith("\\"):
            continue
        parts = path.split("/")
        if ".." in parts:
            continue
        seen.add(path)
    return sorted(seen)


def collect(
    base_sha: str | None,
    subject_sha: str,
    *,
    root: str | Path = ".",
) -> ChangedFiles:
    """Return the changed-file set between ``base_sha`` and ``subject_sha``."""
    if not base_sha:
        return ChangedFiles(files=[], unknown_diff=True, reason="missing_base_sha")
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "diff",
                "--name-only",
                f"{base_sha}...{subject_sha}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ChangedFiles(files=[], unknown_diff=True, reason="git_diff_failed")
    if proc.returncode != 0:
        return ChangedFiles(files=[], unknown_diff=True, reason="git_diff_failed")
    return ChangedFiles(files=_normalize(proc.stdout), unknown_diff=False, reason=None)


def collect_for_context(ctx: EventContext, *, root: str | Path = ".") -> ChangedFiles:
    """Convenience wrapper: collect the diff for a normalized event context."""
    return collect(ctx.base_sha, ctx.subject_sha, root=root)
