from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLICATION = ROOT / ".github/workflows/publish-analysis.yml"


class Phase4WorkflowTests(unittest.TestCase):
    def test_publication_has_explicit_permissions(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r"(?m)^permissions:\s*\n"
                r"\s+actions:\s+read\s*\n"
                r"\s+checks:\s+write\s*\n"
                r"\s+contents:\s+read\s*\n"
                r"(?:\s+#.*\n)*"
                r"\s+security-events:\s+write\s*$"
            ),
        )

    def test_shadow_and_disabled_do_not_publish(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        self.assertIn("if: inputs.mode == 'shadow' || inputs.mode == 'disabled'", text)
        self.assertIn(
            "if: inputs.mode == 'blocking' || inputs.mode == 'advisory'", text
        )

    def test_local_envelopes_are_preflighted_before_remote_publication(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        ordered_steps = (
            "Revalidate downloaded canonical bundle",
            "Render publication payload",
            "Preflight publication and SARIF envelopes",
            "Upload SDK-projected SARIF to code scanning",
            "Create or update GitHub check",
        )
        indexes = [text.index(step) for step in ordered_steps]
        self.assertEqual(sorted(indexes), indexes)

    def test_check_identity_is_stable_across_run_attempts(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        publish = text[text.index("- id: publish") :]
        self.assertIn("run-id: ${{ github.run_id }}", publish)
        self.assertIn("matrix-id: ${{ inputs.matrix-id }}", publish)
        self.assertNotIn("github.run_attempt", publish)

    def test_download_and_integrity_verification_use_the_pinned_retrieval_action(
        self,
    ) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        self.assertIn(
            "Quantum-L9/l9-ci-core/.github/actions/"
            "retrieve-artifacts@def55c54ff4ba654c2ebea088dde71db0b5f7135",
            text,
        )
        for value in (
            "artifact-name: ${{ inputs.artifact-name }}",
            "repository-revision: ${{ inputs.repository-revision }}",
            "sdk-revision: ${{ inputs.sdk-revision }}",
        ):
            self.assertIn(value, text)
        self.assertNotIn(
            "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
            text,
        )


if __name__ == "__main__":
    unittest.main()
