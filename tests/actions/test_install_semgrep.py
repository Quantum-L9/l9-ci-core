"""Hash-locked Semgrep installer (``.github/actions/install-semgrep``).

The central required workflow selects an exact Semgrep version, but an exact
``semgrep==X`` still let pip resolve the rest of the closure against a moving
runner image. The installer therefore refuses any version without a lock file
that names every package, version, and wheel digest, and installs only from it.
These tests keep the lock honest for the version Core selects by default and
keep the installer fail-closed without ever running pip.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "install-semgrep"
INSTALL = ACTION / "install.sh"
LOCKS = ACTION / "locks"
GENERATOR = ACTION / "lock_semgrep.py"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"


def load_generator():
    spec = importlib.util.spec_from_file_location("lock_semgrep", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


class SoleInstallPathTests(unittest.TestCase):
    """This action is the only way Semgrep enters a runner.

    The action exists because `semgrep==X` pins one package and lets pip
    re-resolve the rest of the closure on every run — the failure its own
    install.sh header describes. A workflow that installs Semgrep with raw pip
    reintroduces exactly that, and does so invisibly: Semgrep is then executed
    as a PATH-resolved subprocess of the SDK, so it never appears as a bare
    name in any YAML while still being an unlocked dependency at runtime.
    """

    WORKFLOWS = ROOT / ".github" / "workflows"
    LOCKED_ACTION = (
        "Quantum-L9/l9-ci-core/.github/actions/install-semgrep@"
        "673a3e4c82021809af32baac7571fde5e1059d3b"
    )

    def _sources(self) -> list[Path]:
        return sorted([*self.WORKFLOWS.glob("*.yml"), *self.WORKFLOWS.glob("*.yaml")])

    def test_no_workflow_installs_semgrep_with_raw_pip(self) -> None:
        offenders: list[str] = []
        pattern = re.compile(r"pip\s+install[^\n]*semgrep\s*==")
        for path in self._sources():
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(
            [],
            offenders,
            "install Semgrep through the hash-locked action; a bare "
            "`pip install semgrep==X` re-resolves the closure per run",
        )

    def test_every_semgrep_installing_workflow_uses_the_locked_action(self) -> None:
        """Both installers must be the same pinned revision, not two mechanisms."""
        referencing = {
            path.name
            for path in self._sources()
            if "install-semgrep@" in path.read_text(encoding="utf-8")
        }
        self.assertIn("org-ci.yml", referencing)
        self.assertIn("analyze-semgrep.yml", referencing)
        for name in referencing:
            with self.subTest(workflow=name):
                text = (self.WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIn(
                    self.LOCKED_ACTION,
                    text,
                    f"{name} must pin install-semgrep at its first-commit SHA "
                    "(AGENTS.md section 8 keeps it off @v1)",
                )
                self.assertNotRegex(
                    text,
                    r"install-semgrep@v\d",
                    f"{name}: install-semgrep is a first-commit pin, not a "
                    "moving-major reference",
                )


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


class GeneratorTests(unittest.TestCase):
    """The lock's "regenerate with that script" promise has a real script."""

    def setUp(self) -> None:
        self.module = load_generator()

    def test_every_lock_names_the_shipped_generator(self) -> None:
        self.assertTrue(GENERATOR.is_file())
        for lock in sorted(LOCKS.glob("semgrep-*.txt")):
            with self.subTest(lock=lock.name):
                header = lock.read_text(encoding="utf-8").split("\n\n", 1)[0]
                self.assertIn("lock_semgrep.py", header)

    def test_rendered_lock_round_trips_through_the_installer_parser(self) -> None:
        text = self.module.render(
            "1.2.3",
            "3.12",
            [
                ("pip", "26.2.1", ["a" * 64]),
                ("semgrep", "1.2.3", ["b" * 64, "c" * 64]),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "semgrep-1.2.3.txt"
            lock.write_text(text, encoding="utf-8")
            pins = parse_lock(lock)
        self.assertEqual({"pip": ("26.2.1", 1), "semgrep": ("1.2.3", 2)}, pins)
        self.assertNotIn("sdist", text)

    def test_project_names_are_canonicalised(self) -> None:
        self.assertEqual("annotated-types", self.module.canonical("Annotated_Types"))
        self.assertEqual("ruamel-yaml", self.module.canonical("ruamel.yaml"))

    def test_generator_refuses_inexact_versions_before_any_network(self) -> None:
        for argv in (["--version", "1.171"], ["--version", "latest"]):
            with self.subTest(argv=argv):
                self.assertEqual(2, self.module.main(argv))
        self.assertEqual(2, self.module.main(["--version", "1.0.0", "--python", "312"]))

    def test_generator_platforms_match_the_central_runner_class(self) -> None:
        """org-ci.yml runs on manylinux x86_64; the lock must resolve for it."""
        self.assertTrue(all(p.endswith("x86_64") for p in self.module.PLATFORMS))
        self.assertIn("manylinux_2_34_x86_64", self.module.PLATFORMS)


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
