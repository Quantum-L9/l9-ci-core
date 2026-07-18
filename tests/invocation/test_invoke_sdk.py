from __future__ import annotations
import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github/actions/invoke-sdk/invoke.py"
spec = importlib.util.spec_from_file_location("invoke_sdk", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InvokeSDKTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.executable = self.workspace / "l9-ci"
        self.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.report = self.workspace / "semgrep.json"
        self.report.write_text("{}\n", encoding="utf-8")
        self.root = self.workspace / "repository"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def environment(self, **values: str) -> dict[str, str]:
        result = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "L9_EXECUTABLE": str(self.executable),
            "L9_OPERATION": "semgrep-normalize",
            "L9_INPUT": str(self.report),
            "L9_OUTPUT": str(self.workspace / "bundle.json"),
            "L9_ROOT": str(self.root),
            "L9_SNAPSHOT_ID": "snapshot-1",
            "L9_PROVIDER_VERSION": "1.0.0",
            "L9_IDENTITY_MAP": "",
            "L9_POLICY": "",
            "L9_GENERATED_AT": "",
            "L9_REVISION": "a" * 40,
            "L9_STRICT": "true",
            "L9_REQUIRED": "true",
            "L9_DIRTY": "false",
            "L9_MINIMUM_SDK_VERSION": "",
        }
        result.update(values)
        return result

    def test_semgrep_normalize_command_is_structured(self) -> None:
        with patch.dict(os.environ, self.environment(), clear=True):
            command = module.build_command(self.executable)
        self.assertEqual(
            [str(self.executable), "semgrep", "normalize"],
            command[:3],
        )
        self.assertIn("--strict", command)
        self.assertIn("--required", command)
        self.assertIn("--no-dirty", command)

    def test_unknown_operation_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            self.environment(L9_OPERATION="shell"),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError):
                module.build_command(self.executable)

    def test_path_escape_is_rejected(self) -> None:
        outside = self.workspace.parent / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        try:
            with patch.dict(
                os.environ,
                self.environment(L9_INPUT=str(outside)),
                clear=True,
            ):
                with self.assertRaises(module.InvocationError):
                    module.build_command(self.executable)
        finally:
            outside.unlink(missing_ok=True)

    def test_bundle_validation_command(self) -> None:
        bundle = self.workspace / "bundle.json"
        bundle.write_text("{}\n", encoding="utf-8")
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="bundle-validate",
                L9_INPUT=str(bundle),
            ),
            clear=True,
        ):
            command = module.build_command(self.executable)
        self.assertEqual(
            [
                str(self.executable),
                "bundle",
                "validate",
                str(bundle),
            ],
            command,
        )


if __name__ == "__main__":
    unittest.main()
