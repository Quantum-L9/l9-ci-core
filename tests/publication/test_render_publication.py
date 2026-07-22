from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/render-publication/render.py"
spec = importlib.util.spec_from_file_location("render_publication", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def gate_document(status: str, *, reasons: list[str] | None = None) -> dict:
    blocking = ["f-1"] if status == "fail" else []
    unresolved = ["f-2"] if status == "incomplete" else []
    return {
        "schema": "l9.gate-result/v1",
        "schema_version": "1.0.0",
        "status": status,
        "blocking_finding_ids": blocking,
        "unresolved_finding_ids": unresolved,
        "fatal_provider_ids": [],
        "incomplete_provider_ids": [],
        "reasons": reasons or [],
        "summary": {
            "blocking_count": len(blocking),
            "unresolved_count": len(unresolved),
            "fatal_provider_count": 0,
            "incomplete_provider_count": 0,
        },
    }


class RenderPublicationTests(unittest.TestCase):
    def environment(
        self,
        workspace: Path,
        payload: Path,
        gate: Path,
        output: Path,
        **overrides: str,
    ) -> dict[str, str]:
        values = {
            "GITHUB_WORKSPACE": str(workspace),
            "L9_AGENT_PAYLOAD": str(payload),
            "L9_GATE_RESULT": str(gate),
            "L9_PROFILE": "pr_fast",
            "L9_MODE": "blocking",
            "L9_PROVIDER": "semgrep",
            "L9_SDK_REVISION": "a" * 40,
            "L9_GOVERNANCE_DIGEST": "b" * 64,
            "L9_REPOSITORY_REVISION": "c" * 40,
            "L9_WORKFLOW_RESULT": "success",
            "L9_RUN_URL": "https://github.example/run/1",
            "L9_ARTIFACT_URL": "https://github.example/artifact/1",
            "L9_PUBLICATION_OUTPUT": str(output),
        }
        values.update(overrides)
        return values

    def render(self, status: str, *, mode: str = "blocking") -> dict:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            payload = workspace / "agent-review-payload.json"
            gate = workspace / "gate-result.json"
            output = workspace / "publication.json"
            payload.write_text(
                json.dumps(
                    {
                        "title": "L9 review",
                        "summary": "SDK-generated summary",
                        "annotations": [
                            {
                                "path": "src/example.py",
                                "start_line": 4,
                                "end_line": 4,
                                "annotation_level": "warning",
                                "message": "SDK-generated annotation",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            gate.write_text(
                json.dumps(gate_document(status, reasons=["canonical reason"])),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                self.environment(
                    workspace,
                    payload,
                    gate,
                    output,
                    L9_MODE=mode,
                ),
                clear=True,
            ):
                result = module.main()
            self.assertEqual(0, result)
            return json.loads(output.read_text(encoding="utf-8"))

    def test_pass_is_success(self) -> None:
        publication = self.render("pass")
        self.assertEqual("success", publication["conclusion"])
        self.assertEqual("pass", publication["metadata"]["gate_status"])

    def test_blocking_gate_fail_is_failure(self) -> None:
        publication = self.render("fail", mode="blocking")
        self.assertEqual("failure", publication["conclusion"])
        self.assertIn("canonical reason", publication["output"]["summary"])

    def test_advisory_gate_fail_is_neutral(self) -> None:
        publication = self.render("fail", mode="advisory")
        self.assertEqual("neutral", publication["conclusion"])

    def test_incomplete_and_invalid_block_in_blocking_mode(self) -> None:
        self.assertEqual("failure", self.render("incomplete")["conclusion"])
        self.assertEqual("failure", self.render("invalid")["conclusion"])

    def test_infrastructure_failure_dominates(self) -> None:
        self.assertEqual(
            "failure",
            module.publication_conclusion("blocking", "failure", "pass"),
        )
        self.assertEqual(
            "neutral",
            module.publication_conclusion("advisory", "failure", "pass"),
        )

    def test_malformed_gate_result_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            payload = workspace / "payload.json"
            gate = workspace / "gate.json"
            output = workspace / "out.json"
            payload.write_text("{}\n", encoding="utf-8")
            gate.write_text('{"status":"pass"}\n', encoding="utf-8")
            with patch.dict(
                os.environ,
                self.environment(workspace, payload, gate, output),
                clear=True,
            ):
                self.assertEqual(2, module.main())

    def test_summary_mismatch_fails_closed(self) -> None:
        document = gate_document("fail")
        document["summary"]["blocking_count"] = 0
        with self.assertRaises(module.PublicationError):
            module.validate_gate_result(document)

    def test_annotations_are_bounded(self) -> None:
        annotations = [
            {"path": f"src/{index}.py", "line": 1, "message": "message"}
            for index in range(75)
        ]
        self.assertEqual(
            50, len(module.extract_annotations({"annotations": annotations}))
        )


if __name__ == "__main__":
    unittest.main()
