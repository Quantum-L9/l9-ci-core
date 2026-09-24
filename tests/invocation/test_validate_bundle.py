from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".github" / "actions" / "validate-bundle" / "validate.py"
spec = importlib.util.spec_from_file_location("validate_bundle", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ValidateBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name).resolve()
        self.executable = self.workspace / "l9-ci"
        self.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.bundle = self.workspace / "finding-bundle.json"
        self.bundle.write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def environment(self, **values: str) -> dict[str, str]:
        result = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "L9_EXECUTABLE": str(self.executable),
            "L9_BUNDLE": str(self.bundle),
            "L9_MINIMUM_SDK_VERSION": "2.0.0",
        }
        result.update(values)
        return result

    def test_leaf_action_contains_no_uses_edge(self) -> None:
        action = (MODULE_PATH.parent / "action.yml").read_text(encoding="utf-8")
        self.assertNotIn("uses:", action)
        self.assertIn("${{ github.action_path }}/validate.py", action)

    def test_commands_are_argv_only_sdk_calls(self) -> None:
        commands = module.commands(self.executable, self.bundle, "2.0.0")
        self.assertEqual(
            [str(self.executable), "bundle", "validate", str(self.bundle)],
            commands[0],
        )
        self.assertEqual(
            [
                str(self.executable),
                "compatibility",
                "check",
                "--bundle",
                str(self.bundle),
                "--minimum-SDK-version",
                "2.0.0",
            ],
            commands[1],
        )

    def test_bundle_must_remain_inside_workspace(self) -> None:
        outside = self.workspace.parent / "outside-bundle.json"
        outside.write_text("{}\n", encoding="utf-8")
        try:
            with patch.dict(os.environ, self.environment(), clear=True):
                with self.assertRaises(module.ValidationError):
                    module.validate_bundle(str(outside))
        finally:
            outside.unlink(missing_ok=True)

    @unittest.skipIf(os.name == "nt", "symlink creation varies on Windows")
    def test_bundle_symlink_may_not_escape_workspace(self) -> None:
        outside = self.workspace.parent / "outside-bundle.json"
        link = self.workspace / "linked-bundle.json"
        outside.write_text("{}\n", encoding="utf-8")
        link.symlink_to(outside)
        try:
            with patch.dict(os.environ, self.environment(), clear=True):
                with self.assertRaises(module.ValidationError):
                    module.validate_bundle(str(link))
        finally:
            outside.unlink(missing_ok=True)

    def test_executable_must_be_an_absolute_executable_file(self) -> None:
        with self.assertRaises(module.ValidationError):
            module.validate_executable("l9-ci")
        self.executable.chmod(stat.S_IRUSR | stat.S_IWUSR)
        with self.assertRaises(module.ValidationError):
            module.validate_executable(str(self.executable))

    def test_empty_minimum_omits_the_optional_sdk_flag(self) -> None:
        command = module.commands(self.executable, self.bundle, "")[1]
        self.assertEqual(
            [
                str(self.executable),
                "compatibility",
                "check",
                "--bundle",
                str(self.bundle),
            ],
            command,
        )

    def test_first_sdk_failure_is_returned_without_compatibility_call(self) -> None:
        first = type("Completed", (), {"returncode": 7})()
        with (
            patch.dict(os.environ, self.environment(), clear=True),
            patch.object(module.subprocess, "run", return_value=first) as run,
        ):
            self.assertEqual(7, module.main())
        run.assert_called_once()

    def test_success_runs_both_sdk_checks(self) -> None:
        completed = type("Completed", (), {"returncode": 0})()
        with (
            patch.dict(os.environ, self.environment(), clear=True),
            patch.object(module.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(0, module.main())
        self.assertEqual(2, run.call_count)


if __name__ == "__main__":
    unittest.main()
