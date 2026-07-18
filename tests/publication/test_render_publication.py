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


class RenderPublicationTests(unittest.TestCase):
    def environment(
        self, workspace: Path, payload: Path, output: Path, **overrides: str
    ) -> dict[str, str]:
        values = {
            "GITHUB_WORKSPACE": str(workspace),
            "L9_AGENT_PAYLOAD": str(payload),
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

    def test_sdk_projection_is_consumed_without_finding_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            payload = workspace / "agent-review-payload.json"
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
            with patch.dict(
                os.environ, self.environment(workspace, payload, output), clear=True
            ):
                result = module.main()
            self.assertEqual(0, result)
            publication = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("l9.core-publication/v1", publication["schema"])
            self.assertEqual("success", publication["conclusion"])
            self.assertEqual(1, len(publication["output"]["annotations"]))

    def test_advisory_failure_is_neutral(self) -> None:
        self.assertEqual("neutral", module.workflow_conclusion("advisory", "failure"))

    def test_blocking_failure_is_failure(self) -> None:
        self.assertEqual("failure", module.workflow_conclusion("blocking", "failure"))

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
