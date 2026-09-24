from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/retrieve-artifacts/action.yml"


class RetrieveActionTests(unittest.TestCase):
    def test_downloaders_are_sha_pinned_and_verification_follows_both(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        references = re.findall(
            r"uses: actions/download-artifact@([0-9a-f]{40})(?:\s+#.*)?$",
            text,
            re.MULTILINE,
        )
        self.assertEqual(2, len(references))
        self.assertEqual(1, len(set(references)))
        current = text.index("name: Download exact current-run analysis artifact")
        descriptor = text.index("name: Download descriptor artifact by immutable ID")
        verify = text.index("name: Verify complete artifact integrity index")
        self.assertLess(current, descriptor)
        self.assertLess(descriptor, verify)
        current_block = text[current:descriptor]
        descriptor_block = text[descriptor:verify]
        self.assertIn("name: ${{ steps.prepare.outputs.artifact-name }}", current_block)
        self.assertNotIn("artifact-ids:", current_block)
        self.assertIn(
            "artifact-ids: ${{ steps.prepare.outputs.artifact-id }}",
            descriptor_block,
        )
        self.assertIn("github-token: ${{ inputs.token }}", descriptor_block)
        self.assertIn(
            "repository: ${{ steps.prepare.outputs.source-repository }}",
            descriptor_block,
        )
        self.assertIn(
            "run-id: ${{ steps.prepare.outputs.source-run-id }}", descriptor_block
        )
        self.assertNotIn("name:", descriptor_block.split("with:", 1)[1])
        self.assertNotIn("pattern:", text[current:verify])
        self.assertNotIn("merge-multiple:", text[current:verify])

    def test_descriptor_server_preflight_precedes_immutable_id_download(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        preflight = text.index("name: Verify descriptor artifact on GitHub")
        download = text.index("name: Download descriptor artifact by immutable ID")
        self.assertLess(preflight, download)
        block = text[preflight:download]
        self.assertIn('python3 "${{ github.action_path }}/verify_server.py"', block)
        for output in (
            "source-repository",
            "source-run-id",
            "artifact-id",
            "artifact-name",
            "archive-digest",
            "workflow-head-sha",
        ):
            self.assertIn(f"steps.prepare.outputs.{output}", block)

    def test_modes_are_mutually_exclusive_without_caller_source_fields(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        self.assertIn("handoff-descriptor:", text)
        self.assertIn("token:", text)
        for forbidden in (
            "source-repository:",
            "source-run-id:",
            "artifact-id:",
            "artifact-url:",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotRegex(text, rf"(?m)^  {re.escape(forbidden)}$")
        self.assertIn("L9_ARTIFACT_NAME: ${{ inputs.artifact-name }}", text)
        self.assertIn("L9_HANDOFF_DESCRIPTOR: ${{ inputs.handoff-descriptor }}", text)

    def test_action_does_not_reference_an_unpublished_core_revision(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        self.assertNotIn("Quantum-L9/l9-ci-core/", text)
        self.assertIn('python3 "${{ github.action_path }}/prepare.py"', text)
        self.assertIn('python3 "${{ github.action_path }}/verify_server.py"', text)
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
