"""The generated l9-ci shim must expose the provisioned venv's binaries on PATH.

``install_semgrep_runtime`` deliberately installs the Semgrep runtime into the
provisioned venv because, in its own words, "a provisioned SDK that only carries
the import-time requirements cannot run it". The SDK then resolves the provider
with ``shutil.which("semgrep")`` -- a PATH lookup.

The shim used to export ``PYTHONPATH`` only. So Core installed the binary and
then made it unreachable: every ``l9-ci semgrep run`` under Core provisioning
produced a canonical bundle recording a fatal, required provider failure of type
``not_installed``, with the binary sitting unused in ``venv/bin``.

Core's own ``sdk-contract-check.yml`` cannot catch this. Its end-to-end smoke
drives ``semgrep normalize`` against a committed report precisely so that "no
live semgrep binary is required", which means the one command that needs PATH is
the one command CI never runs. Hence these tests.
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
        with (
            patch.object(module, "run", lambda command, **kwargs: None),
            patch.object(
                module, "install_semgrep_runtime", lambda checkout, python: []
            ),
        ):
            return module.create_runtime(self.checkout, self.runtime)

    @unittest.skipIf(os.name == "nt", "POSIX shim")
    def test_shim_prepends_the_venv_bin_directory_to_path(self) -> None:
        executable = self._build_shim()
        body = executable.read_text(encoding="utf-8")
        expected = str(self.runtime / "venv" / "bin")

        self.assertIn(f'export PATH="{expected}${{PATH:+:$PATH}}"', body)

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
    def test_shim_resolves_semgrep_from_the_provisioned_venv(self) -> None:
        """End-to-end on the shim itself: run it and let it resolve a binary.

        A stub ``semgrep`` is placed in the venv's bin directory and the shim is
        pointed at a stub interpreter that reports what ``shutil.which`` finds.
        This is the behaviour the SDK depends on, exercised through the real
        generated shim rather than asserted from its text alone.
        """
        executable = self._build_shim()
        bin_directory = self.runtime / "venv" / "bin"
        bin_directory.mkdir(parents=True, exist_ok=True)

        semgrep = bin_directory / "semgrep"
        semgrep.write_text("#!/usr/bin/env bash\necho stub-semgrep\n", encoding="utf-8")
        semgrep.chmod(0o755)

        # create_runtime writes `exec "<venv>/bin/python" -m l9_ci "$@"`. Supply a
        # stub at that exact path which prints the resolved semgrep location.
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
            env={"PATH": "/usr/bin:/bin"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(semgrep))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
