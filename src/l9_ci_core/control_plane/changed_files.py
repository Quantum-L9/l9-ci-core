"""Changed-file acquisition (stage skeleton).

Runs ``git diff --name-only <base>...<subject>`` with argument arrays (never
shell interpolation), normalizes to sorted, de-duplicated, repository-relative
POSIX paths, and fails closed to ``unknown_diff`` on diff failure
(``schemas/changed-files.schema.json``).

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton.
"""

from __future__ import annotations

from typing import Any

__all__ = ["collect_changed_files"]


def collect_changed_files(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return a changed-files document. Not yet implemented."""
    raise NotImplementedError(
        "collect_changed_files is implemented in a later PR-B commit"
    )
