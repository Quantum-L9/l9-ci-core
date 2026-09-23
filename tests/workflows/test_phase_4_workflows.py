from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHECK_PUBLICATION = ROOT / ".github/workflows/publish-analysis.yml"
SARIF_PUBLICATION = ROOT / ".github/workflows/publish-sarif.yml"
SELF_ANALYSIS = ROOT / ".github/workflows/self-analysis.yml"


def load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in document:
        document["on"] = document.pop(True)
    return document


class Phase4WorkflowTests(unittest.TestCase):
    def test_publication_capabilities_default_false_and_are_permission_scoped(
        self,
    ) -> None:
        check = load(CHECK_PUBLICATION)
        sarif = load(SARIF_PUBLICATION)
        self.assertFalse(
            check["on"]["workflow_call"]["inputs"]["publish-check"]["default"]
        )
        self.assertFalse(
            sarif["on"]["workflow_call"]["inputs"]["publish-sarif"]["default"]
        )

        check_permissions = check["jobs"]["publish-check"]["permissions"]
        sarif_permissions = sarif["jobs"]["publish-sarif"]["permissions"]
        self.assertEqual("write", check_permissions["checks"])
        self.assertNotIn("security-events", check_permissions)
        self.assertEqual("write", sarif_permissions["security-events"])
        self.assertNotIn("checks", sarif_permissions)

    def test_shadow_and_disabled_have_no_remote_write_path(self) -> None:
        for path, job_id, election in (
            (CHECK_PUBLICATION, "publish-check", "inputs.publish-check"),
            (SARIF_PUBLICATION, "publish-sarif", "inputs.publish-sarif"),
        ):
            with self.subTest(path=path.name):
                document = load(path)
                publish_if = document["jobs"][job_id]["if"]
                self.assertIn(election, publish_if)
                self.assertIn("inputs.mode == 'blocking'", publish_if)
                self.assertIn("inputs.mode == 'advisory'", publish_if)
                suppressed = document["jobs"]["suppressed"]
                self.assertEqual({"contents": "read"}, suppressed["permissions"])
                suppressed_if = suppressed["if"]
                self.assertIn("inputs.mode == 'shadow'", suppressed_if)
                self.assertIn("inputs.mode == 'disabled'", suppressed_if)

    def test_check_publication_preflights_before_one_remote_write(self) -> None:
        text = CHECK_PUBLICATION.read_text(encoding="utf-8")
        ordered_steps = (
            "Retrieve and verify immutable analysis artifact",
            "Revalidate downloaded canonical bundle",
            "Render publication payload",
            "Preflight publication envelope",
            "Create or update GitHub check",
        )
        indexes = [text.index(step) for step in ordered_steps]
        self.assertEqual(sorted(indexes), indexes)
        self.assertEqual(1, text.count("name: Create or update GitHub check"))
        self.assertNotIn("upload-sarif@", text)
        self.assertNotIn("Checkout immutable event revision", text)
        self.assertIn(
            "gate-result: ${{ steps.artifacts.outputs.gate-result }}",
            text,
        )

    def test_sarif_publication_preflights_before_one_remote_write(self) -> None:
        text = SARIF_PUBLICATION.read_text(encoding="utf-8")
        ordered_steps = (
            "Retrieve and verify immutable analysis artifact",
            "Revalidate downloaded canonical bundle",
            "Require elected SARIF route",
            "Preflight SARIF envelope",
            "Upload SDK-projected SARIF to code scanning",
        )
        indexes = [text.index(step) for step in ordered_steps]
        self.assertEqual(sorted(indexes), indexes)
        self.assertEqual(1, text.count("upload-sarif@"))
        self.assertNotIn("Create or update GitHub check", text)
        self.assertNotIn("Checkout immutable event revision", text)

    def test_check_identity_is_stable_across_run_attempts(self) -> None:
        text = CHECK_PUBLICATION.read_text(encoding="utf-8")
        publish = text[text.index("- id: publish") :]
        self.assertIn("run-id: ${{ github.run_id }}", publish)
        self.assertIn("matrix-id: ${{ inputs.matrix-id }}", publish)
        self.assertNotIn("github.run_attempt", publish)

    def test_publication_consumes_optional_handoff_descriptor(self) -> None:
        for path in (CHECK_PUBLICATION, SARIF_PUBLICATION):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("artifact-handoff:", text)
                self.assertIn(
                    "handoff-descriptor: ${{ inputs.artifact-handoff }}", text
                )
                self.assertIn(
                    "retrieve-artifacts@05e842683d54a54ad074d9f4f9105813c42b6954",
                    text,
                )

    def test_trusted_caller_publishes_terminal_blocking_result(self) -> None:
        text = SELF_ANALYSIS.read_text(encoding="utf-8")
        job = load(SELF_ANALYSIS)["jobs"]["publish-check"]
        self.assertIn("always()", job["if"])
        self.assertIn("needs.analyze.outputs.evidence-ready == 'true'", job["if"])
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            job["if"],
        )
        self.assertEqual("write", job["permissions"]["checks"])
        self.assertNotIn("security-events", job["permissions"])
        self.assertEqual("${{ needs.analyze.result }}", job["with"]["workflow-result"])
        self.assertTrue(job["with"]["publish-check"])
        self.assertNotIn("pull_request_target:", text)


if __name__ == "__main__":
    unittest.main()
