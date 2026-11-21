"""invoke-sdk passes Core's central finding-waiver ledger, and only Core's."""

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
spec = importlib.util.spec_from_file_location("invoke_sdk_waivers", MODULE_PATH)
assert spec is not None
assert spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

REPOSITORY = "Quantum-L9/l9-ci-sdk"


class FindingWaiverInvocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.workspace = base / "workspace"
        self.workspace.mkdir()
        self.runner_temp = base / "runner-temp"
        self.runner_temp.mkdir()
        self.executable = self.workspace / "l9-ci"
        self.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.report = self.workspace / "semgrep.json"
        self.report.write_text("{}\n", encoding="utf-8")
        self.ledger = self.runner_temp / "finding-waivers.yaml"
        self.ledger.write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def environment(self, **values: str) -> dict[str, str]:
        result = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "L9_EXECUTABLE": str(self.executable),
            "L9_OPERATION": "semgrep-normalize",
            "L9_INPUT": str(self.report),
            "L9_OUTPUT": str(self.workspace / "bundle.json"),
            "L9_ROOT": ".",
            "L9_SNAPSHOT_ID": "snapshot-1",
            "L9_PROVIDER_VERSION": "1.0.0",
            "L9_STRICT": "true",
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

    def test_no_ledger_passes_no_waiver_flag(self) -> None:
        command = self.command()
        for flag in ("--waivers", "--repository", "--waiver-date"):
            self.assertNotIn(flag, command)

    def test_ledger_and_repository_reach_both_semgrep_operations(self) -> None:
        for extra in (
            {},
            {"L9_OPERATION": "semgrep-run", "L9_LANGUAGE": "python"},
        ):
            with self.subTest(operation=extra.get("L9_OPERATION", "normalize")):
                command = self.command(
                    L9_WAIVERS=str(self.ledger),
                    L9_WAIVER_REPOSITORY=REPOSITORY,
                    L9_WAIVER_DATE="2026-11-21",
                    **extra,
                )
                self.assertEqual(
                    str(self.ledger), command[command.index("--waivers") + 1]
                )
                self.assertEqual(REPOSITORY, command[command.index("--repository") + 1])
                self.assertEqual(
                    "2026-11-21", command[command.index("--waiver-date") + 1]
                )

    def test_ledger_inside_the_workspace_is_refused(self) -> None:
        planted = self.workspace / "finding-waivers.yaml"
        planted.write_text("{}\n", encoding="utf-8")
        message = self.refused(L9_WAIVERS=str(planted), L9_WAIVER_REPOSITORY=REPOSITORY)
        self.assertIn("outside GITHUB_WORKSPACE", message)

    def test_symlinked_ledger_is_refused(self) -> None:
        link = self.runner_temp / "link.yaml"
        link.symlink_to(self.ledger)
        self.assertIn(
            "regular file",
            self.refused(L9_WAIVERS=str(link), L9_WAIVER_REPOSITORY=REPOSITORY),
        )

    def test_relative_or_missing_ledger_is_refused(self) -> None:
        self.assertIn(
            "absolute",
            self.refused(
                L9_WAIVERS="finding-waivers.yaml", L9_WAIVER_REPOSITORY=REPOSITORY
            ),
        )
        self.assertIn(
            "regular file",
            self.refused(
                L9_WAIVERS=str(self.runner_temp / "absent.yaml"),
                L9_WAIVER_REPOSITORY=REPOSITORY,
            ),
        )

    def test_ledger_needs_a_well_formed_repository(self) -> None:
        for repository in ("", "l9-ci-sdk", "Quantum-L9/l9-ci-sdk/extra"):
            with self.subTest(repository=repository):
                self.assertIn(
                    "owner",
                    self.refused(
                        L9_WAIVERS=str(self.ledger),
                        L9_WAIVER_REPOSITORY=repository,
                    ),
                )

    def test_repository_or_date_without_a_ledger_is_refused(self) -> None:
        self.assertIn("require waivers", self.refused(L9_WAIVER_REPOSITORY=REPOSITORY))
        self.assertIn("require waivers", self.refused(L9_WAIVER_DATE="2026-11-21"))

    def test_malformed_waiver_date_is_refused(self) -> None:
        self.assertIn(
            "YYYY-MM-DD",
            self.refused(
                L9_WAIVERS=str(self.ledger),
                L9_WAIVER_REPOSITORY=REPOSITORY,
                L9_WAIVER_DATE="21/11/2026",
            ),
        )

    def test_action_maps_every_waiver_input_to_the_adapter(self) -> None:
        action = (ROOT / ".github/actions/invoke-sdk/action.yml").read_text(
            encoding="utf-8"
        )
        for name, variable in (
            ("waivers", "L9_WAIVERS"),
            ("waiver-repository", "L9_WAIVER_REPOSITORY"),
            ("waiver-date", "L9_WAIVER_DATE"),
        ):
            self.assertIn(f"  {name}:\n", action)
            self.assertIn(f"{variable}: ${{{{ inputs.{name} }}}}", action)


if __name__ == "__main__":
    unittest.main()
