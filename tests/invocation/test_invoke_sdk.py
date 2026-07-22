from __future__ import annotations

import importlib.util
import json
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
        self.bundle = self.workspace / "bundle.json"
        self.bundle.write_text("{}\n", encoding="utf-8")
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
            "L9_OUTPUT": str(self.workspace / "bundle-out.json"),
            "L9_ROOT": str(self.root),
            "L9_SNAPSHOT_ID": "snapshot-1",
            "L9_PROVIDER_VERSION": "1.170.0",
            "L9_IDENTITY_MAP": "",
            "L9_POLICY": "",
            "L9_GENERATED_AT": "2026-07-21T13:46:27Z",
            "L9_REVISION": "a" * 40,
            "L9_STRICT": "true",
            "L9_REQUIRED": "true",
            "L9_DIRTY": "false",
            "L9_MINIMUM_SDK_VERSION": "",
            "L9_ARGUMENTS_JSON": "[]",
            "L9_TIMEOUT_SECONDS": "900",
            "L9_OUTPUT_SIZE_LIMIT_BYTES": "50000000",
        }
        result.update(values)
        return result

    def test_semgrep_normalize_requires_version_and_timestamp(self) -> None:
        with patch.dict(os.environ, self.environment(), clear=True):
            command = module.build_command(self.executable)
        self.assertEqual([str(self.executable), "semgrep", "normalize"], command[:3])
        self.assertIn("--provider-version", command)
        self.assertIn("--generated-at", command)
        self.assertIn("--strict", command)
        self.assertIn("--required", command)
        self.assertIn("--no-dirty", command)

    def test_semgrep_run_passes_discrete_provider_arguments(self) -> None:
        report_output = self.workspace / "raw/report.json"
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="semgrep-run",
                L9_INPUT=str(report_output),
                L9_ARGUMENTS_JSON=json.dumps(["--config", "p/python"]),
                L9_PROVIDER_VERSION="",
            ),
            clear=True,
        ):
            command = module.build_command(self.executable)
        self.assertEqual([str(self.executable), "semgrep", "run"], command[:3])
        self.assertIn("--report", command)
        self.assertIn("--execution-arg=--config", command)
        self.assertIn("--execution-arg=p/python", command)
        self.assertNotIn("--provider-version", command)

    def test_arguments_json_rejects_non_string_values(self) -> None:
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="semgrep-run",
                L9_INPUT=str(self.workspace / "raw/report.json"),
                L9_ARGUMENTS_JSON='["--config", 7]',
                L9_PROVIDER_VERSION="",
            ),
            clear=True,
        ):
            with self.assertRaises(module.InvocationError):
                module.build_command(self.executable)

    def test_gate_evaluate_command_is_structured(self) -> None:
        gate = self.workspace / "gate-result.json"
        with patch.dict(
            os.environ,
            self.environment(
                L9_OPERATION="gate-evaluate",
                L9_INPUT=str(self.bundle),
                L9_OUTPUT=str(gate),
            ),
            clear=True,
        ):
            command = module.build_command(self.executable)
        self.assertEqual([str(self.executable), "gate", "evaluate"], command[:3])
        self.assertIn("--strict-unresolved", command)

    def test_gate_status_requires_protocol_and_matching_exit(self) -> None:
        gate = self.workspace / "gate-result.json"
        gate.write_text(
            json.dumps(
                {
                    "schema": "l9.gate-result/v1",
                    "schema_version": "1.0.0",
                    "status": "fail",
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual("fail", module.gate_status_for_exit(gate, 1))
        self.assertIsNone(module.gate_status_for_exit(gate, 0))
        gate.write_text('{"status":"fail"}', encoding="utf-8")
        self.assertIsNone(module.gate_status_for_exit(gate, 1))

    def test_gate_semantic_exit_is_action_success(self) -> None:
        executable = self.workspace / "semantic-gate"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "out.write_text(json.dumps({'schema':'l9.gate-result/v1','schema_version':'1.0.0','status':'fail'}))\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        with patch.dict(
            os.environ,
            self.environment(
                L9_EXECUTABLE=str(executable),
                L9_OPERATION="gate-evaluate",
                L9_INPUT=str(self.bundle),
                L9_OUTPUT=str(self.workspace / "gate-result.json"),
            ),
            clear=True,
        ):
            self.assertEqual(0, module.main())

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


if __name__ == "__main__":
    unittest.main()
