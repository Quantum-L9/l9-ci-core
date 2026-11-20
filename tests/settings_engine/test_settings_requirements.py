"""The settings runner installs the engine's whole closure, hash-locked.

tools/l9_settings imports PyYAML and jsonschema. The runner used to install
requirements-ci.txt (PyYAML only, no hashes), so jsonschema was missing and
nothing was verified. requirements-settings.txt is that closure for the
runner's CPython 3.12.14 on Linux x86_64, each entry equal to the
provision-sdk runtime lock's.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "requirements-settings.txt"
SDK_LOCK = (
    ROOT
    / ".github"
    / "actions"
    / "provision-sdk"
    / "locks"
    / "cpython-3.12.14-linux-x86_64.txt"
)
RUNNER = ROOT / ".github" / "workflows" / "settings-runner.yml"
ENGINE = ROOT / "tools" / "l9_settings"
REQUIREMENT = re.compile(r"^([A-Za-z0-9._-]+)==(\S+) \\$")
HASH = re.compile(r"^\s+--hash=sha256:([0-9a-f]{64})$")


def entries(path: Path) -> dict[str, tuple[str, str]]:
    """name -> (version, sha256); every requirement carries exactly one hash."""
    found: dict[str, tuple[str, str]] = {}
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    for requirement, digest in zip(lines[0::2], lines[1::2], strict=True):
        name = REQUIREMENT.match(requirement)
        hashed = HASH.match(digest)
        if name is None or hashed is None:
            raise AssertionError(f"{path.name}: unhashed or malformed: {requirement!r}")
        found[name.group(1).lower()] = (name.group(2), hashed.group(1))
    return found


class SettingsRequirementsTests(unittest.TestCase):
    def test_every_entry_matches_the_sdk_runtime_lock(self) -> None:
        settings = entries(SETTINGS)
        lock = entries(SDK_LOCK)
        for name, pinned in settings.items():
            with self.subTest(name=name):
                self.assertEqual(lock.get(name), pinned)

    def test_the_engine_imports_are_covered(self) -> None:
        third_party: set[str] = set()
        for module in ENGINE.glob("*.py"):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    third_party |= {alias.name.split(".")[0] for alias in node.names}
                elif (
                    isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
                ):
                    third_party.add(node.module.split(".")[0])
        distributions = {"yaml": "pyyaml", "jsonschema": "jsonschema"}
        settings = entries(SETTINGS)
        for module, distribution in distributions.items():
            with self.subTest(module=module):
                self.assertIn(module, third_party)
                self.assertIn(distribution, settings)

    def test_the_runner_installs_it_hash_locked(self) -> None:
        workflow = yaml.safe_load(RUNNER.read_text(encoding="utf-8"))
        runs = [
            " ".join(str(step.get("run", "")).split())
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if "pip install" in str(step.get("run", ""))
        ]
        self.assertTrue(runs)
        for run in runs:
            with self.subTest(run=run):
                self.assertIn("--require-hashes", run)
                self.assertIn("--only-binary :all:", run)
                self.assertIn("requirements-settings.txt", run)
                self.assertNotIn("requirements-ci.txt", run)


if __name__ == "__main__":
    unittest.main()
