"""Core-run advisory Biome check (``.github/actions/run-biome-check``).

The executable is a shim with a chosen exit code, so these tests pin the exit
class contract without network or a real Biome: ``0`` pass, ``1`` findings
(advisory exit 0), anything else or an unstartable file is a Core
infrastructure failure (non-zero). They also pin that Core never captures or
parses Biome's report.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "run-biome-check"
RUN = ACTION / "check_biome.py"
REPORT = Path(".l9") / "runtime" / "biome-check.json"


class RunBiomeCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.summary = self.tmp / "summary.md"

    def _biome(self, exit_code: int) -> Path:
        shim = self.tmp / f"biome-{exit_code}"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            'echo "$@" > "$(dirname "$0")/argv"\n'
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)
        return shim

    def _configured(self) -> None:
        (self.workspace / "biome.json").write_text(
            '{\n  "$schema": "https://biomejs.dev/schemas/2.5.8/schema.json"\n}\n',
            encoding="utf-8",
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["GITHUB_WORKSPACE"] = str(self.workspace)
        env["GITHUB_STEP_SUMMARY"] = str(self.summary)
        return subprocess.run(
            [sys.executable, "-I", str(RUN), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def _record(self) -> dict[str, object]:
        return json.loads((self.workspace / REPORT).read_text(encoding="utf-8"))

    def test_no_biome_config_is_not_applicable_without_an_executable(self) -> None:
        proc = self._run("--mode", "advisory")
        self.assertEqual(0, proc.returncode, proc.stderr)
        record = self._record()
        self.assertEqual("not_applicable", record["status"])
        self.assertIsNone(record["biome_exit_code"])
        self.assertIn("not_applicable", self.summary.read_text(encoding="utf-8"))

    def test_biome_jsonc_counts_as_configuration(self) -> None:
        (self.workspace / "biome.jsonc").write_text("{}\n", encoding="utf-8")
        proc = self._run("--mode", "advisory", "--executable", str(self._biome(0)))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("pass", self._record()["status"])

    def test_clean_run_records_pass_and_uses_biomes_own_reporter(self) -> None:
        self._configured()
        biome = self._biome(0)
        proc = self._run("--mode", "advisory", "--executable", str(biome))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(
            {"status": "pass", "biome_exit_code": 0},
            {key: self._record()[key] for key in ("status", "biome_exit_code")},
        )
        argv = (self.tmp / "argv").read_text(encoding="utf-8").split()
        self.assertEqual(["ci", "--reporter=github", "."], argv)

    def test_findings_are_recorded_and_advisory_exits_zero(self) -> None:
        self._configured()
        proc = self._run(
            "--mode",
            "advisory",
            "--executable",
            str(self._biome(1)),
            "--provider-version",
            "2.5.8",
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        record = self._record()
        self.assertEqual("findings", record["status"])
        self.assertEqual(1, record["biome_exit_code"])
        self.assertEqual("2.5.8", record["provider_version"])

    def test_biome_crash_exit_is_an_infrastructure_failure(self) -> None:
        self._configured()
        proc = self._run("--mode", "advisory", "--executable", str(self._biome(101)))
        self.assertEqual(3, proc.returncode)
        self.assertEqual("error", self._record()["status"])
        self.assertEqual(101, self._record()["biome_exit_code"])

    def test_missing_executable_is_an_infrastructure_failure(self) -> None:
        self._configured()
        for executable in ("", str(self.tmp / "absent"), "relative/biome"):
            with self.subTest(executable=executable):
                proc = self._run("--mode", "advisory", "--executable", executable)
                self.assertEqual(3, proc.returncode)
                self.assertIn("infrastructure failure", proc.stderr)

    def test_corrupt_executable_is_an_infrastructure_failure(self) -> None:
        self._configured()
        corrupt = self.tmp / "biome-corrupt"
        corrupt.write_bytes(b"\x7fELF\x02\x01\x01\x00garbage-not-a-program")
        corrupt.chmod(0o755)
        proc = self._run("--mode", "advisory", "--executable", str(corrupt))
        self.assertEqual(3, proc.returncode, proc.stderr)

    def test_undefined_mode_is_refused(self) -> None:
        self._configured()
        proc = self._run("--mode", "blocking", "--executable", str(self._biome(0)))
        self.assertEqual(2, proc.returncode)
        self.assertIn("only advisory", proc.stderr)

    def test_root_outside_the_workspace_is_refused(self) -> None:
        proc = self._run("--mode", "advisory", "--root", "..")
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("escapes the workspace", proc.stderr)


class NoReportParsingTests(unittest.TestCase):
    """Core passes Biome's output through; it never reads it."""

    def test_run_does_not_capture_or_parse_biome_output(self) -> None:
        text = RUN.read_text(encoding="utf-8")
        for token in ("capture_output", "PIPE", "check_output", "--reporter=json"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_action_is_a_local_leaf_with_no_nested_remote_edge(self) -> None:
        action = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))
        self.assertEqual("composite", action["runs"]["using"])
        self.assertEqual("advisory", action["inputs"]["mode"]["default"])
        for step in action["runs"]["steps"]:
            self.assertNotIn("uses", step)
        self.assertNotIn("l9-biome-scan", (ACTION / "action.yml").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
