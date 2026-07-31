from __future__ import annotations
import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "actions"
    / "provision-sdk"
    / "provision.py"
)
spec = importlib.util.spec_from_file_location("provision_sdk", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SDK_V1 = "f546f122d33601ea5a4b2592e3482c5c39eddd82"


class ManifestEntrySelectionTests(unittest.TestCase):
    def test_selects_entry_with_full_cli_surface(self) -> None:
        entry = module.select_manifest_entry(SDK_V1)
        self.assertEqual(entry["integration_contract"], "l9.integration-contract/v1")
        for path in (
            "semgrep run",
            "semgrep normalize",
            "gate evaluate",
            "bundle validate",
            "bundle project-agent-payload",
            "compatibility check",
            "baseline compare-tests",
            "baseline scan-packet-envelope",
            "baseline compare-scan",
            "baseline validate-ledger",
        ):
            with self.subTest(path=path):
                self.assertIn(path, entry["required_cli_paths"])

    def test_unlisted_revision_has_no_entry(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            module.select_manifest_entry("0" * 40)


class ProbeCliTests(unittest.TestCase):
    """probe_cli must execute EVERY declared required_cli_path, not a hardcoded
    subset — the manifest is the executable contract."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp.name).resolve()
        self.log = self.workdir / "probes.log"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fake_executable(self, *, fail_on: str | None = None) -> Path:
        # Records every invocation to self.log; exits non-zero when the first
        # argument matches ``fail_on`` (simulating a missing subcommand).
        exe = self.workdir / "l9-ci"
        guard = ""
        if fail_on is not None:
            guard = f'if [ "$1" = "{fail_on}" ]; then exit 2; fi\n'
        exe.write_text(
            f'#!/usr/bin/env bash\necho "$@" >> "{self.log}"\n{guard}exit 0\n',
            encoding="utf-8",
        )
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        return exe

    def test_probes_every_declared_path(self) -> None:
        exe = self._fake_executable()
        paths = ["semgrep run", "gate evaluate", "baseline compare-tests"]
        module.probe_cli(exe, paths)
        logged = self.log.read_text(encoding="utf-8")
        self.assertIn("--help", logged)  # root probe
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(f"{path} --help", logged)

    def test_missing_declared_path_fails_closed(self) -> None:
        exe = self._fake_executable(fail_on="gate")
        with self.assertRaises(module.ProvisioningError) as caught:
            module.probe_cli(exe, ["semgrep run", "gate evaluate"])
        self.assertIn("gate evaluate", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
