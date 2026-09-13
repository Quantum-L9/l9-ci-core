"""Hash-locked Semgrep installer (``.github/actions/install-semgrep``).

The central required workflow selects an exact Semgrep version, but an exact
``semgrep==X`` still let pip resolve the rest of the closure against a moving
runner image. The installer therefore refuses any version without a lock file
that names every package, version, and wheel digest, and installs only from it.
These tests keep the lock honest for the version Core selects by default and
keep the installer fail-closed without ever running pip.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "install-semgrep"
INSTALL = ACTION / "install.sh"
LOCKS = ACTION / "locks"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"

REQUIREMENT = re.compile(r"^([a-z0-9][a-z0-9-]*)==(\S+) \\$")
HASH = re.compile(r"^    --hash=sha256:[0-9a-f]{64}( \\)?$")


def central_semgrep_version() -> str:
    document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    triggers = document[True] if True in document else document["on"]
    versions = {
        str(triggers[trigger]["inputs"]["semgrep-version"]["default"])
        for trigger in ("workflow_dispatch", "workflow_call")
    }
    assert len(versions) == 1, versions
    return versions.pop()


def parse_lock(path: Path) -> dict[str, tuple[str, int]]:
    """Return ``{project: (version, digest_count)}`` and reject malformed lines."""
    pins: dict[str, tuple[str, int]] = {}
    current: str | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#"):
            current = None
            continue
        requirement = REQUIREMENT.match(line)
        if requirement:
            current = requirement.group(1)
            if current in pins:
                raise AssertionError(f"{path.name}:{number}: duplicate {current}")
            pins[current] = (requirement.group(2), 0)
            continue
        if HASH.match(line) and current is not None:
            version, count = pins[current]
            pins[current] = (version, count + 1)
            continue
        raise AssertionError(
            f"{path.name}:{number}: unlocked or malformed line: {line!r}"
        )
    return pins


class LockContractTests(unittest.TestCase):
    def test_default_central_version_has_a_lock(self) -> None:
        version = central_semgrep_version()
        lock = LOCKS / f"semgrep-{version}.txt"
        self.assertTrue(lock.is_file(), f"missing lock for central default: {lock}")
        pins = parse_lock(lock)
        self.assertEqual(version, pins["semgrep"][0])

    def test_every_lock_pins_exact_versions_with_digests(self) -> None:
        locks = sorted(LOCKS.glob("semgrep-*.txt"))
        self.assertTrue(locks, "no lock files shipped with the action")
        for lock in locks:
            with self.subTest(lock=lock.name):
                version = lock.name[len("semgrep-") : -len(".txt")]
                self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
                pins = parse_lock(lock)
                self.assertEqual(version, pins["semgrep"][0])
                self.assertIn("pip", pins, "pip itself must be part of the lock")
                for project, (pinned, digests) in pins.items():
                    self.assertRegex(pinned, r"^[0-9A-Za-z.!+-]+$", project)
                    self.assertGreaterEqual(digests, 1, f"{project} has no digest")


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copy(INSTALL, self.tmp / "install.sh")
        (self.tmp / "locks").mkdir()
        # A pip shim that records any invocation: the refusal paths must exit
        # before pip is ever reached, and this proves it rather than assumes it.
        shim = self.tmp / "bin"
        shim.mkdir()
        (shim / "python").write_text(
            '#!/usr/bin/env bash\necho invoked >> "$PIP_TRACE"\nexit 0\n',
            encoding="utf-8",
        )
        (shim / "python").chmod(0o755)
        self.trace = self.tmp / "pip-trace"

    def run_installer(self, version: str) -> subprocess.CompletedProcess[str]:
        import os

        env = dict(os.environ)
        env["L9_SEMGREP_VERSION"] = version
        env["PATH"] = f"{self.tmp / 'bin'}:{env['PATH']}"
        env["PIP_TRACE"] = str(self.trace)
        return subprocess.run(
            ["bash", str(self.tmp / "install.sh")],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_unlocked_version_is_refused_before_pip(self) -> None:
        proc = self.run_installer("9.9.9")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("no locked install contract for semgrep 9.9.9", proc.stderr)
        self.assertFalse(self.trace.exists(), "pip ran for an unlocked version")

    def test_inexact_version_is_refused_before_pip(self) -> None:
        for version in ("", "1.171", "latest", "1.171.0;rm"):
            with self.subTest(version=version):
                proc = self.run_installer(version)
                self.assertEqual(2, proc.returncode, proc.stderr)
                self.assertIn("exact x.y.z", proc.stderr)
        self.assertFalse(self.trace.exists())

    def test_lock_that_does_not_pin_the_requested_semgrep_is_refused(self) -> None:
        (self.tmp / "locks" / "semgrep-1.0.0.txt").write_text(
            "requests==2.0.0 \\\n    --hash=sha256:" + "0" * 64 + "\n",
            encoding="utf-8",
        )
        proc = self.run_installer("1.0.0")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("does not pin semgrep==1.0.0", proc.stderr)
        self.assertFalse(self.trace.exists())

    def test_installer_requires_hashes_and_wheels_only(self) -> None:
        text = INSTALL.read_text(encoding="utf-8")
        self.assertIn("--require-hashes", text)
        self.assertIn("--only-binary :all:", text)
        self.assertNotRegex(text, r"pip install[^\n]*--upgrade")

    def test_action_declares_the_version_input_and_runs_the_installer(self) -> None:
        action = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))
        self.assertTrue(action["inputs"]["semgrep-version"]["required"])
        self.assertEqual("composite", action["runs"]["using"])
        steps = action["runs"]["steps"]
        self.assertEqual(1, len(steps))
        self.assertIn("install.sh", steps[0]["run"])
        self.assertEqual(
            "${{ inputs.semgrep-version }}", steps[0]["env"]["L9_SEMGREP_VERSION"]
        )


if __name__ == "__main__":
    unittest.main()
