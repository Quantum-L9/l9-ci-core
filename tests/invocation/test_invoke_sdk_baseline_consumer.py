"""invoke-sdk: the baseline commit (SDK ADR 0023) and the consumer run (ADR 0021)."""

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
spec = importlib.util.spec_from_file_location("invoke_sdk_baseline", MODULE_PATH)
assert spec is not None
assert spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

BASELINE = "a" * 40


class InvocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name).resolve()
        self.executable = self.workspace / "l9-ci"
        self.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.policy = self.workspace / "policy.yaml"
        self.policy.write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def environment(self, **values: str) -> dict[str, str]:
        result = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "L9_EXECUTABLE": str(self.executable),
            "L9_OPERATION": "semgrep-run",
            "L9_LANGUAGE": "python",
            "L9_OUTPUT": str(self.workspace / "bundle.json"),
            "L9_ROOT": ".",
            "L9_SNAPSHOT_ID": "snapshot-1",
            "L9_POLICY": str(self.policy),
            "L9_REVISION": "b" * 40,
            "L9_STRICT": "false",
            "L9_REQUIRED": "true",
            "L9_DIRTY": "false",
        }
        result.update(values)
        return result

    def command(self, **values: str) -> list[str]:
        with patch.dict(os.environ, self.environment(**values), clear=True):
            return module.build_command(self.executable)

    def refused(self, **values: str) -> str:
        with patch.dict(os.environ, self.environment(**values), clear=True):
            with self.assertRaises(module.InvocationError) as caught:
                module.build_command(self.executable)
        return str(caught.exception)


class BaselineCommitTests(InvocationTests):
    def test_no_baseline_passes_no_flag(self) -> None:
        self.assertNotIn("--baseline-commit", self.command())

    def test_a_baseline_reaches_semgrep_run_with_its_policy(self) -> None:
        command = self.command(L9_BASELINE_COMMIT=BASELINE)
        self.assertEqual(BASELINE, command[command.index("--baseline-commit") + 1])
        self.assertIn("--policy", command)

    def test_a_short_or_malformed_baseline_is_refused(self) -> None:
        for value in ("abc1234", "A" * 40, "g" * 40, "HEAD^1"):
            with self.subTest(value=value):
                self.assertIn("full 40", self.refused(L9_BASELINE_COMMIT=value))

    def test_a_baseline_without_policy_is_refused(self) -> None:
        message = self.refused(L9_BASELINE_COMMIT=BASELINE, L9_POLICY="")
        self.assertIn("requires policy", message)

    def test_the_baseline_is_ignored_by_other_operations(self) -> None:
        command = self.command(
            L9_OPERATION="semgrep-consumer-run", L9_BASELINE_COMMIT=BASELINE
        )
        self.assertNotIn("--baseline-commit", command)


class ConsumerRunTests(InvocationTests):
    def test_the_consumer_run_is_its_own_command(self) -> None:
        command = self.command(
            L9_OPERATION="semgrep-consumer-run",
            L9_RAW_OUTPUT=str(self.workspace / "raw" / "consumer.json"),
        )
        self.assertEqual([str(self.executable), "semgrep", "consumer-run"], command[:3])
        for flag in ("--output", "--root", "--snapshot-id", "--raw-output"):
            self.assertIn(flag, command)
        self.assertEqual(str(self.policy), command[command.index("--policy") + 1])
        self.assertIn("--required", command)
        self.assertIn("--no-dirty", command)
        self.assertNotIn("--language", command)

    def test_the_consumer_run_needs_a_snapshot_and_a_workspace_output(self) -> None:
        self.assertIn(
            "snapshot-id",
            self.refused(L9_OPERATION="semgrep-consumer-run", L9_SNAPSHOT_ID=""),
        )
        self.assertIn(
            "GITHUB_WORKSPACE",
            self.refused(
                L9_OPERATION="semgrep-consumer-run",
                L9_OUTPUT=str(self.workspace.parent / "outside.json"),
            ),
        )

    def test_the_consumer_run_requires_the_core_provider(self) -> None:
        with patch.dict(
            os.environ,
            self.environment(L9_OPERATION="semgrep-consumer-run"),
            clear=True,
        ):
            self.assertEqual(2, module.main())

    def test_the_action_maps_the_new_inputs(self) -> None:
        action = (ROOT / ".github/actions/invoke-sdk/action.yml").read_text("utf-8")
        self.assertIn("L9_BASELINE_COMMIT: ${{ inputs.baseline-commit }}", action)
        self.assertIn("semgrep-consumer-run", action)


if __name__ == "__main__":
    unittest.main()
