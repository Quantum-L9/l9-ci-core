from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/retrieve-artifacts/action.yml"


class RetrieveActionTests(unittest.TestCase):
    def test_downloader_is_sha_pinned_and_verification_follows_download(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r"uses: actions/download-artifact@[0-9a-f]{40}(?:\s+#.*)?$",
                re.MULTILINE,
            ),
        )
        download = text.index("name: Download exact analysis artifact")
        verify = text.index("name: Verify complete artifact integrity index")
        self.assertLess(download, verify)
        self.assertIn("name: ${{ inputs.artifact-name }}", text[download:verify])
        self.assertNotIn("pattern:", text[download:verify])
        self.assertNotIn("merge-multiple:", text[download:verify])

    def test_action_does_not_reference_an_unpublished_core_revision(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        self.assertNotIn("Quantum-L9/l9-ci-core/", text)
        self.assertIn('python3 "${{ github.action_path }}/prepare.py"', text)
        self.assertIn('python3 "${{ github.action_path }}/retrieve.py"', text)

    def test_public_outputs_are_verified_routes(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        for output in (
            "artifact-root",
            "index",
            "bundle",
            "agent-payload",
            "raw-directory",
            "routing-record",
            "sarif",
        ):
            with self.subTest(output=output):
                self.assertIn(
                    f"value: ${{{{ steps.verify.outputs.{output} }}}}",
                    text,
                )


if __name__ == "__main__":
    unittest.main()
