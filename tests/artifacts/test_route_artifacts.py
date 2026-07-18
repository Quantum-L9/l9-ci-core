from __future__ import annotations
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/route-artifacts/route.py"
spec = importlib.util.spec_from_file_location("route_artifacts", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RouteArtifactTests(unittest.TestCase):
    def test_canonical_files_are_copied_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            raw = workspace / "source/raw.json"
            bundle = workspace / "source/bundle.json"
            payload = workspace / "source/payload.json"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b'{"raw":true}\n')
            bundle.write_bytes(b'{"bundle":true}\n')
            payload.write_bytes(b'{"payload":true}\n')
            environment = {
                "GITHUB_WORKSPACE": str(workspace),
                "L9_PROVIDER": "semgrep",
                "L9_MATRIX_ID": "python-3.12",
                "L9_RAW_REPORT": str(raw),
                "L9_BUNDLE": str(bundle),
                "L9_AGENT_PAYLOAD": str(payload),
                "L9_DESTINATION_ROOT": "artifacts",
            }
            with patch.dict(os.environ, environment, clear=True):
                result = module.main()
            self.assertEqual(0, result)
            routed_bundle = workspace / "artifacts/l9/python-3.12/finding-bundle.json"
            routed_payload = (
                workspace / "artifacts/l9/python-3.12/agent-review-payload.json"
            )
            self.assertEqual(digest(bundle), digest(routed_bundle))
            self.assertEqual(digest(payload), digest(routed_payload))

    def test_invalid_matrix_identifier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "source.json"
            source.write_text("{}\n", encoding="utf-8")
            environment = {
                "GITHUB_WORKSPACE": str(workspace),
                "L9_PROVIDER": "semgrep",
                "L9_MATRIX_ID": "../../escape",
                "L9_RAW_REPORT": str(source),
                "L9_BUNDLE": str(source),
                "L9_AGENT_PAYLOAD": str(source),
                "L9_DESTINATION_ROOT": "artifacts",
            }
            with patch.dict(os.environ, environment, clear=True):
                result = module.main()
            self.assertEqual(2, result)


if __name__ == "__main__":
    unittest.main()
