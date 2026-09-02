from __future__ import annotations
import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

SDK_V1 = "7d7762eae5e1a12fdc66276975e2949891762a20"


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
            "bundle project-sarif",
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


class SemgrepRuntimeProvisioningTests(unittest.TestCase):
    """`semgrep run` executes the semgrep binary, so provisioning must install a
    Semgrep runtime into the SDK venv, pinned from the pinned SDK checkout."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.checkout = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_pyproject(self, body: str) -> None:
        (self.checkout / "pyproject.toml").write_text(body, encoding="utf-8")

    def test_requirements_sourced_from_sdk_optional_group(self) -> None:
        self._write_pyproject(
            "[project]\n"
            'name = "l9-ci-sdk"\n'
            "[project.optional-dependencies]\n"
            'semgrep = ["semgrep==1.171.0"]\n'
        )
        self.assertEqual(
            module.resolve_semgrep_requirements(self.checkout),
            ["semgrep==1.171.0"],
        )

    def test_requirements_fall_back_when_no_group_declared(self) -> None:
        self._write_pyproject('[project]\nname = "l9-ci-sdk"\n')
        self.assertEqual(
            module.resolve_semgrep_requirements(self.checkout),
            [module.SEMGREP_RUNTIME_FALLBACK],
        )

    def test_install_semgrep_runtime_pip_installs_specifiers(self) -> None:
        self._write_pyproject(
            "[project]\n"
            'name = "l9-ci-sdk"\n'
            "[project.optional-dependencies]\n"
            'semgrep = ["semgrep==1.171.0"]\n'
        )
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return None

        venv_python = self.checkout / "venv" / "bin" / "python"
        with patch.object(module, "run", fake_run):
            installed = module.install_semgrep_runtime(self.checkout, venv_python)
        self.assertEqual(installed, ["semgrep==1.171.0"])
        self.assertEqual(len(calls), 1)
        command = calls[0]
        self.assertEqual(
            command[:5],
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--quiet",
            ],
        )
        self.assertIn("semgrep==1.171.0", command)


if __name__ == "__main__":
    unittest.main()
