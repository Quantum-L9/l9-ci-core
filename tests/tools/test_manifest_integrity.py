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

import hashlib
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.__main__ import (  # noqa: E402
    MANIFEST_CHECK_ENV,
    WorkflowError,
    manifest_check_enabled,
    reseal_checksum_manifest,
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

    def test_reseal_rewrites_listed_digests_from_current_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            listed = root / "org-ci.yml"
            listed.write_text("name: before\n", encoding="utf-8")
            stale = "0" * 64
            (root / "MANIFEST.sha256").write_text(
                f"{stale}  org-ci.yml\n", encoding="utf-8"
            )
            self.assertTrue(reseal_checksum_manifest(root))
            verify_checksum_manifest(root)
            self.assertFalse(reseal_checksum_manifest(root))

    def test_reseal_fails_closed_on_a_non_hex_digest_field(self) -> None:
        """A 64-character non-hex digest is corruption, not staleness.

        The verifier already rejects it before comparing bytes. Reseal must
        reject it too: rewriting the field would replace the evidence of
        corruption with a freshly computed digest, so a tampered manifest
        would come back green on the next Dependabot reseal.

        The digest here is shape-valid — exactly 64 characters, correct
        two-space separator, real listed path — and differs from a legitimate
        entry by a single non-hex character. A check that only measured the
        field's length or the line's shape would accept it.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            listed = root / "org-ci.yml"
            listed.write_text("name: before\n", encoding="utf-8")
            corrupt = "0" * 63 + "g"
            self.assertEqual(len(corrupt), 64)
            manifest = root / "MANIFEST.sha256"
            original = f"{corrupt}  org-ci.yml\n"
            manifest.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(WorkflowError, "invalid sha256 digest"):
                reseal_checksum_manifest(root)

            self.assertEqual(
                manifest.read_text(encoding="utf-8"),
                original,
                "reseal must fail before rewriting a corrupt manifest",
            )

    def test_reseal_and_verify_reject_the_same_digest_fields(self) -> None:
        """Reseal's fail-closed surface must not be weaker than the verifier's.

        Both read the same manifest; a field one accepts and the other rejects
        is a hole that lets an automated reseal launder a manifest the
        verifier would have refused.
        """
        for label, digest in (
            ("non-hex tail", "0" * 63 + "g"),
            ("uppercase hex", "A" * 64),
            ("non-ascii", "0" * 63 + "é"),
        ):
            with self.subTest(digest=label):
                with tempfile.TemporaryDirectory() as temporary:
                    root = pathlib.Path(temporary)
                    (root / "org-ci.yml").write_text("name: before\n", encoding="utf-8")
                    (root / "MANIFEST.sha256").write_text(
                        f"{digest}  org-ci.yml\n", encoding="utf-8"
                    )
                    with self.assertRaises(WorkflowError):
                        verify_checksum_manifest(root)
                    with self.assertRaises(WorkflowError):
                        reseal_checksum_manifest(root)

    def test_workflow_byte_change_without_reseal_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            listed = root / "org-ci.yml"
            listed.write_text("name: before\n", encoding="utf-8")
            digest = hashlib.sha256(b"name: before\n").hexdigest()
            (root / "MANIFEST.sha256").write_text(
                f"{digest}  org-ci.yml\n", encoding="utf-8"
            )
            verify_checksum_manifest(root)
            listed.write_text("name: after\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "checksum mismatch"):
                verify_checksum_manifest(root)


if __name__ == "__main__":
    unittest.main()
