"""``MANIFEST.sha256`` must stay honest about the tracked worktree.

``make validate`` already calls :func:`verify_checksum_manifest`, but nothing
on the pull-request path does: ``self-ci.yml`` and ``governance-ci.yml`` both
run ``unittest discover`` and never invoke the repository facade. A dependency
bump or a docs edit that skips the manifest therefore merges green and only
surfaces later, on someone's local ``make validate`` or in Phase 4 release
validation.

That is not hypothetical: #81 and #82 bumped ``pyproject.toml``,
``requirements-ci.txt`` and ``requirements-repo-runtime.txt`` without
regenerating their entries, leaving ``main`` unable to pass ``make validate``.
This test runs the same checker the facade runs, so the drift is caught on the
pull request that introduces it.

This test follows the same ``L9_MANIFEST_CHECK`` switch as the facade, so one
variable governs both. The switch defaults to enabled: disabling it is for
bisects and salvage work on a knowingly drifted tree, not for landing a change
without regenerating the manifest.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.__main__ import (  # noqa: E402
    MANIFEST_CHECK_ENV,
    WorkflowError,
    manifest_check_enabled,
    verify_checksum_manifest,
)


@unittest.skipUnless(
    manifest_check_enabled(),
    f"manifest verification disabled via {MANIFEST_CHECK_ENV}",
)
class ManifestIntegrityTests(unittest.TestCase):
    def test_tracked_manifest_matches_the_worktree(self) -> None:
        try:
            verify_checksum_manifest(ROOT)
        except WorkflowError as error:
            self.fail(
                f"{error}\n\nRegenerate the MANIFEST.sha256 entries for every "
                "file you changed — see AGENTS.md and "
                "docs/repository-execution-runtime.md."
            )


if __name__ == "__main__":
    unittest.main()
