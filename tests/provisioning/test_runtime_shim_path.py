"""The SDK shim must not install or shadow Core's provider executable.

Core owns the exact tool pin and installs Semgrep through one hash-locked
provider action. The SDK venv contains SDK import dependencies only, while the
shim inherits the caller's PATH so the SDK resolves Core's selected provider.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
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
        self.lock = root / "sdk-runtime-lock.txt"
        self.lock.write_text("PyYAML==6.0.3 --hash=sha256:" + "a" * 64 + "\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build_shim(self) -> Path:
        """Create the runtime with venv creation and pip installs stubbed out."""
        with patch.object(module, "run", lambda command, **kwargs: None):
            return module.create_runtime(self.checkout, self.runtime, self.lock)

    @unittest.skipIf(os.name == "nt", "POSIX shim")
    def test_shim_does_not_override_the_callers_path(self) -> None:
        executable = self._build_shim()
        body = executable.read_text(encoding="utf-8")

        self.assertNotIn("export PATH=", body)

    @unittest.skipIf(os.name == "nt", "POSIX shim")
    def test_shim_uses_isolated_python_without_inheriting_pythonpath(self) -> None:
        body = self._build_shim().read_text(encoding="utf-8")

        self.assertIn("unset PYTHONPATH PYTHONHOME", body)
        self.assertIn("-I -m l9_ci", body)
        self.assertNotIn(str(self.checkout), body)

    def test_verified_checkout_is_registered_inside_the_venv(self) -> None:
        self._build_shim()
        source_link = (
            self.runtime
            / "venv"
            / "lib"
            / f"python{module.sys.version_info.major}.{module.sys.version_info.minor}"
            / "site-packages"
            / "l9-ci-sdk-source.pth"
        )
        self.assertEqual(
            f"{self.checkout.resolve()}\n", source_link.read_text(encoding="utf-8")
        )

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

        result = subprocess.run(
            [str(executable)],
            check=False,
            text=True,
            capture_output=True,
            env={"PATH": f"{core_bin}:/usr/bin:/bin"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(core_semgrep))

    @unittest.skipIf(os.name == "nt", "POSIX launcher")
    def test_isolated_launcher_cannot_import_a_consumer_shadow_package(self) -> None:
        (self.checkout / "l9_ci").mkdir()
        (self.checkout / "l9_ci" / "__main__.py").write_text(
            'print("verified-sdk")\n', encoding="utf-8"
        )
        consumer = self.runtime.parent / "consumer"
        (consumer / "l9_ci").mkdir(parents=True)
        (consumer / "l9_ci" / "__main__.py").write_text(
            'print("consumer-shadow")\n', encoding="utf-8"
        )
        subprocess.run(
            [module.sys.executable, "-m", "venv", str(self.runtime / "venv")],
            check=True,
        )
        with patch.object(module, "run", lambda command, **kwargs: None):
            launcher = module.create_runtime(self.checkout, self.runtime, self.lock)
        result = subprocess.run(
            [str(launcher)],
            cwd=consumer,
            check=False,
            text=True,
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(consumer)},
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("verified-sdk", result.stdout.strip())

    def test_runtime_installs_only_the_core_owned_wheel_lock(self) -> None:
        requirements = self.checkout / "requirements.txt"
        requirements.write_text("PyYAML==6.0.3\n", encoding="utf-8")
        calls: list[list[str]] = []

        def record(command: list[str], **kwargs) -> None:
            calls.append(command)

        with patch.object(module, "run", record):
            module.create_runtime(self.checkout, self.runtime, self.lock)

        pip_commands = [command for command in calls if "pip" in command]
        self.assertEqual(1, len(pip_commands))
        self.assertIn(str(self.lock), pip_commands[0])
        self.assertNotIn(str(requirements), pip_commands[0])
        self.assertIn("--only-binary", pip_commands[0])
        self.assertIn("--require-hashes", pip_commands[0])
        self.assertNotIn("semgrep", " ".join(pip_commands[0]).lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
