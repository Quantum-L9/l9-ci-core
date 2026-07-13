"""Changed-file acquisition.

Derives the deterministic, repository-relative changed-file set for a
normalized event context (see ``schemas/changed-files.schema.json``). A diff
failure yields an ``unknown_diff`` result rather than raising, so planning can
still produce a conservative fail-closed plan.

Implemented in PR-B2.
"""
from __future__ import annotations
