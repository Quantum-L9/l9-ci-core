from __future__ import annotations
import re
import unittest
from pathlib import Path
from typing import Any
import yaml
ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "analyze-semgrep.yml"
COMPATIBILITY = ROOT / ".l9" / "sdk-compatibility.yaml"
SDK_SHA = "0c487747b0fcd172edaefe9e843dac818de8fc12"
LEGACY_SHA = "0779fca8238011f8abea551895f96584676e9d17"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
EXTERNAL_USE = re.compile(
    r"(?m)^\s*uses:\s+([^@\s]+)@([^\s#]+)"
)
def load_compatibility() -> dict[str, Any]:
    document = yaml.safe_load(COMPATIBILITY.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("SDK compatibility manifest must be an object")
    return document
class SDKHandoffContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.compatibility = load_compatibility()
    def test_workflow_is_reusable(self) -> None:
        self.assertIn("workflow_call:", self.workflow)
        required_inputs = (
            "profile:",
            "matrix-id:",
            "language:",
            "semgrep-version:",
            "repository-revision:",
            "sdk-revision:",
            "retention-days:",
            "publish:",
        )
        for value in required_inputs:
            with self.subTest(value=value):
                self.assertIn(value, self.workflow)
    def test_workflow_exposes_handoff_outputs(self) -> None:
        required_outputs = (
            "artifact-name:",
            "bundle-path:",
            "gate-result-path:",
            "agent-payload-path:",
            "workflow-conclusion:",
        )
        for value in required_outputs:
            with self.subTest(value=value):
                self.assertIn(value, self.workflow)
    def test_workflow_checks_out_caller_and_own_core_source(self) -> None:
        self.assertIn(
            "ref: ${{ inputs.repository-revision }}",
            self.workflow,
        )
        self.assertIn(
            "repository: ${{ job.workflow_repository }}",
            self.workflow,
        )
        self.assertIn(
            "ref: ${{ job.workflow_sha }}",
            self.workflow,
        )
        self.assertIn(
            "path: .l9/runtime/core-control-plane",
            self.workflow,
        )
    def test_workflow_uses_one_sdk_revision_input(self) -> None:
        self.assertIn(
            f"default: {SDK_SHA}",
            self.workflow,
        )
        self.assertGreaterEqual(
            self.workflow.count("sdk-revision: ${{ inputs.sdk-revision }}"),
            2,
        )
        self.assertNotIn(
            "sdk-revision: 0779fca8238011f8abea551895f96584676e9d17",
            self.workflow,
        )
    def test_pipeline_order_preserves_ownership_boundary(self) -> None:
        ordered_steps = (
            "Resolve governance",
            "Provision immutable SDK",
            "Run Semgrep and normalize through the SDK",
            "Validate canonical bundle",
            "Evaluate canonical SDK gate",
            "Project SDK agent-review payload",
            "Route raw and canonical artifacts",
            "Route canonical gate result",
            "Build artifact manifest",
            "Upload complete analysis artifact set",
        )
        positions = [
            self.workflow.index(step)
            for step in ordered_steps
        ]
        self.assertEqual(sorted(positions), positions)
    def test_core_invokes_sdk_run_instead_of_parsing_semgrep(self) -> None:
        self.assertIn("semgrep run", self.workflow)
        self.assertNotIn("semgrep normalize", self.workflow)
        self.assertNotIn("json.loads(raw_report", self.workflow)
        self.assertNotIn("finding-bundle.schema", self.workflow)
    def test_gate_is_emitted_before_projection_and_upload(self) -> None:
        gate = self.workflow.index("Evaluate canonical SDK gate")
        projection = self.workflow.index(
            "Project SDK agent-review payload"
        )
        upload = self.workflow.index(
            "Upload complete analysis artifact set"
        )
        self.assertLess(gate, projection)
        self.assertLess(gate, upload)
        self.assertIn("gate-result.json", self.workflow)
        self.assertIn(
            'canonical["gate_result"]',
            self.workflow,
        )
    def test_publication_consumes_sdk_derived_result(self) -> None:
        self.assertIn(
            "workflow-result: >-",
            self.workflow,
        )
        self.assertIn(
            "${{ needs.analyze.outputs['workflow-conclusion'] }}",
            self.workflow,
        )
        self.assertIn(
            "/.github/actions/render-publication",
            self.workflow,
        )
        self.assertIn(
            "/.github/actions/publish-check",
            self.workflow,
        )
    def test_write_permission_is_owned_by_the_caller(self) -> None:
        self.assertNotIn("checks: write", self.workflow)
        self.assertIn("contents: read", self.workflow)
    def test_external_actions_are_immutably_pinned(self) -> None:
        references = EXTERNAL_USE.findall(self.workflow)
        self.assertTrue(references)
        for action, revision in references:
            with self.subTest(action=action):
                self.assertRegex(revision, FULL_SHA)
    def test_compatibility_manifest_promotes_handoff_sdk(self) -> None:
        self.assertEqual(
            "l9.sdk-compatibility/v1",
            self.compatibility["schema"],
        )
        default = self.compatibility["default"]
        self.assertEqual(SDK_SHA, default["revision"])
        self.assertEqual(
            "l9.integration-contract/v1",
            default["integration_contract"],
        )
        self.assertIn(
            "l9.gate-result/v1",
            default["artifact_protocols"],
        )
        required_cli_paths = set(default["required_cli_paths"])
        self.assertTrue(
            {
                "semgrep run",
                "bundle validate",
                "bundle project-agent-payload",
                "compatibility check",
                "gate evaluate",
            }.issubset(required_cli_paths)
        )
    def test_handoff_manifest_matches_workflow(self) -> None:
        handoff = self.compatibility["handoff"]
        self.assertEqual(
            ".github/workflows/analyze-semgrep.yml",
            handoff["reusable_workflow"],
        )
        self.assertEqual(SDK_SHA, handoff["SDK_revision"])
        self.assertEqual("Core", handoff["provider_execution_owner"])
        self.assertEqual("SDK", handoff["gate_decision_owner"])
        self.assertEqual("Core", handoff["publication_owner"])
        self.assertEqual(
            {
                "python",
                "typescript",
            },
            set(handoff["supported_languages"]),
        )
        self.assertEqual(
            {
                "pr_fast",
                "merge",
                "nightly",
                "release",
                "supply_chain",
            },
            set(handoff["required_profiles"]),
        )
    def test_current_and_rollback_revisions_are_supported(self) -> None:
        supported = {
            entry["revision"]
            for entry in self.compatibility["supported"]
        }
        self.assertIn(SDK_SHA, supported)
        self.assertIn(LEGACY_SHA, supported)
        for revision in supported:
            with self.subTest(revision=revision):
                self.assertRegex(revision, FULL_SHA)
    def test_drift_mechanisms_remain_disabled(self) -> None:
        policy = self.compatibility["policy"]
        disabled = (
            "arbitrary_install_commands_allowed",
            "floating_git_references_allowed",
            "short_git_revisions_allowed",
            "branches_allowed",
            "tags_allowed",
            "unlisted_revisions_allowed",
            "fallback_to_parent_allowed",
            "fallback_to_legacy_cli_allowed",
            "Core_must_not_reconstruct_gate_result",
        )
        for name in disabled[:-1]:
            with self.subTest(name=name):
                self.assertIs(False, policy[name])
        self.assertIs(
            True,
            policy["Core_must_not_reconstruct_gate_result"],
        )
        self.assertIs(
            True,
            policy["one_SDK_revision_per_analysis_run"],
        )
        self.assertIs(
            True,
            policy["reusable_workflow_requires_immutable_reference"],
        )
if __name__ == "__main__":
    unittest.main()
