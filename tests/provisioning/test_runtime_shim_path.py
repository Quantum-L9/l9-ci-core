"""The SDK shim must not install or shadow Core's provider executable.

Core owns the exact tool pin and installs Semgrep through one hash-locked
provider action. The SDK venv contains SDK import dependencies only, while the
shim inherits the caller's PATH so the SDK resolves Core's selected provider.
"""

from __future__ import annotations

import importlib.util
import os
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


class RuntimeShimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.checkout = root / "source"
        self.checkout.mkdir()
        self.runtime = root / "runtime"
        self.runtime.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build_shim(self) -> Path:
        """Create the runtime with venv creation and pip installs stubbed out."""
        with patch.object(module, "run", lambda command, **kwargs: None):
            return module.create_runtime(self.checkout, self.runtime)

    @unittest.skipIf(os.name == "nt", "POSIX shim")
    def test_shim_does_not_override_the_callers_path(self) -> None:
        executable = self._build_shim()
        body = executable.read_text(encoding="utf-8")

        self.assertNotIn("export PATH=", body)

    @unittest.skipIf(os.name == "nt", "POSIX shim")
    def test_shim_still_exports_pythonpath_for_the_source_checkout(self) -> None:
        """The PATH fix must not displace the reason the shim exists."""
        body = self._build_shim().read_text(encoding="utf-8")

        self.assertIn(f'export PYTHONPATH="{self.checkout}', body)
        self.assertIn("-m l9_ci", body)

    @unittest.skipIf(os.name == "nt", "POSIX shim")
    def test_shim_is_executable(self) -> None:
        executable = self._build_shim()
        self.assertTrue(executable.stat().st_mode & stat.S_IXUSR)

    @unittest.skipIf(os.name == "nt", "POSIX shim")
    def test_shim_resolves_semgrep_from_the_callers_path(self) -> None:
        executable = self._build_shim()
        bin_directory = self.runtime / "venv" / "bin"
        bin_directory.mkdir(parents=True, exist_ok=True)

        sdk_semgrep = bin_directory / "semgrep"
        sdk_semgrep.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
        sdk_semgrep.chmod(0o755)

        core_bin = self.runtime.parent / "core-bin"
        core_bin.mkdir()
        core_semgrep = core_bin / "semgrep"
        core_semgrep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        core_semgrep.chmod(0o755)

        python = bin_directory / "python"
        python.write_text(
            '#!/usr/bin/env bash\ncommand -v semgrep || echo "NOT-ON-PATH"\n',
            encoding="utf-8",
        )
        python.chmod(0o755)

        import subprocess

        result = subprocess.run(
            [str(executable)],
            check=False,
            text=True,
            capture_output=True,
            env={"PATH": f"{core_bin}:/usr/bin:/bin"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(core_semgrep))

    def test_runtime_creation_never_installs_semgrep(self) -> None:
        requirements = self.checkout / "requirements.txt"
        requirements.write_text("PyYAML==6.0.3\n", encoding="utf-8")
        calls: list[list[str]] = []

        def record(command: list[str], **kwargs) -> None:
            calls.append(command)

        with patch.object(module, "run", record):
            module.create_runtime(self.checkout, self.runtime)

        pip_commands = [command for command in calls if "pip" in command]
        self.assertEqual(1, len(pip_commands))
        self.assertIn(str(requirements), pip_commands[0])
        self.assertNotIn("semgrep", " ".join(pip_commands[0]).lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
