from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/normalize-semgrep-report.yml"
CORE_ACTION = re.compile(
    r"^\s*uses:\s*Quantum-L9/l9-ci-core/(\.github/actions/[A-Za-z0-9._/-]+)@"
    r"([0-9a-f]{40})\s*$",
    re.MULTILINE,
)
LOCAL_CORE_ACTION = re.compile(r"^\s*uses:\s*\./\.github/actions/", re.MULTILINE)


class Phase2WorkflowTests(unittest.TestCase):
    def test_reusable_workflow_is_present(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)

    def test_workflow_uses_read_only_permissions(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(r"(?m)^permissions:\s*\n\s+contents:\s+read\s*$"),
        )
        self.assertNotIn(": write", text)

    def test_sdk_pipeline_order_is_explicit(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        markers = [
            "Normalize provider report",
            "Validate canonical bundle",
            "Project agent-review payload",
            "Route artifacts",
            "Revalidate routed canonical bundle",
            "Build artifact manifest",
            "Upload Phase 2 artifact set",
            "Create artifact handoff descriptor",
        ]
        positions = [text.index(marker) for marker in markers]
        self.assertEqual(sorted(positions), positions)

    def test_upload_action_is_immutable(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            text,
        )

    def test_core_actions_are_fully_qualified_immutable_pins(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIsNone(
            LOCAL_CORE_ACTION.search(text),
            "caller-relative Core actions resolve against the consumer checkout",
        )

        references = CORE_ACTION.findall(text)
        self.assertEqual(8, len(references))
        self.assertEqual(
            {
                ".github/actions/build-artifact-manifest",
                ".github/actions/create-artifact-handoff",
                ".github/actions/invoke-sdk",
                ".github/actions/provision-sdk",
                ".github/actions/route-artifacts",
                ".github/actions/validate-bundle",
            },
            {path for path, _revision in references},
        )
        self.assertEqual(
            1,
            len({revision for _path, revision in references}),
            "Phase 2 Core primitives must advance together to one immutable revision",
        )

    def test_complete_relocatable_index_is_built_before_upload(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        build = text.index("name: Build artifact manifest")
        upload = text.index("name: Upload Phase 2 artifact set")
        self.assertLess(build, upload)
        index_block = text[build:upload]
        self.assertIn(
            "artifact-name: ${{ steps.names.outputs.artifact-name }}", index_block
        )
        self.assertIn("repository: ${{ github.repository }}", index_block)
        self.assertIn("repository-revision: ${{ github.sha }}", index_block)
        self.assertIn("artifact-root: artifacts", index_block)
        self.assertIn(
            "routing-record: ${{ steps.route.outputs.routing-record }}",
            index_block,
        )
        self.assertIn("artifact-index.json", index_block)
        self.assertNotIn("artifact-manifest.json", index_block)

    def test_handoff_is_additive_and_created_only_from_upload_outputs(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        upload = text.index("name: Upload Phase 2 artifact set")
        handoff = text.index("name: Create artifact handoff descriptor")
        self.assertLess(upload, handoff)
        block = text[handoff:]
        self.assertIn("artifact-id: ${{ steps.upload.outputs.artifact-id }}", block)
        self.assertIn(
            "artifact-digest: ${{ steps.upload.outputs.artifact-digest }}", block
        )
        self.assertIn("repository: ${{ github.repository }}", block)
        self.assertIn("repository-revision: ${{ github.sha }}", block)
        self.assertIn(
            "output: .l9/runtime/handoffs/${{ inputs.matrix-id }}.json", block
        )
        header = text[: text.index("permissions:")]
        self.assertIn("artifact-handoff:", header)
        self.assertIn("value: ${{ jobs.normalize.outputs.artifact-handoff }}", header)

    def test_retrieval_is_not_faked_inside_the_producer_workflow(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn(
            "Quantum-L9/l9-ci-core/.github/actions/retrieve-artifacts@",
            text,
        )


if __name__ == "__main__":
    unittest.main()
