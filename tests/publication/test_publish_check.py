from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/publish-check/publish.py"
spec = importlib.util.spec_from_file_location("publish_check", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublishCheckTests(unittest.TestCase):
    def test_valid_publication_document(self) -> None:
        document = {
            "schema": "l9.core-publication/v1",
            "name": "L9 review",
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "success",
            "output": {
                "title": "L9 review",
                "summary": "summary",
                "text": "",
                "annotations": [],
            },
            "metadata": {"run_url": "https://example.invalid/run"},
        }
        self.assertIs(document, module.validate_document(document))

    def test_too_many_annotations_are_rejected(self) -> None:
        document = {
            "schema": "l9.core-publication/v1",
            "name": "L9 review",
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "success",
            "output": {
                "title": "L9 review",
                "summary": "summary",
                "annotations": [{} for _ in range(51)],
            },
        }
        with self.assertRaises(module.CheckPublicationError):
            module.validate_document(document)


if __name__ == "__main__":
    unittest.main()
