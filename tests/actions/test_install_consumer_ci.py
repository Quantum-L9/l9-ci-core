"""Identity tests for the consumer CI installer action.

Pin strings must stay identical to Core's declared lock. Do not invent versions.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "install-consumer-ci"
PINS = ACTION / "requirements-consumer-ci.txt"
LOCK = ACTION / "toolchain-lock.json"
TOOLS = ACTION / "core-owned-tools.json"
INSTALL = ACTION / "install.sh"
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
BIOME = ROOT / "presets" / "typescript" / "biome.json"
ROOT_INCLUDE = ROOT / "requirements-consumer-ci.txt"
REPO_RUNTIME = ROOT / "requirements-repo-runtime.txt"

PIN_RE = re.compile(r"^(ruff|mypy|pytest)==([0-9][^\s]+)$", re.M)


def _pins() -> dict[str, str]:
    text = PINS.read_text(encoding="utf-8")
    found = dict(PIN_RE.findall(text))
    if set(found) != {"ruff", "mypy", "pytest"}:
        raise AssertionError(f"pin file missing exact toolchain lines: {found}")
    return found


class InstallConsumerCiTests(unittest.TestCase):
    def test_pin_file_and_lock_json_match(self) -> None:
        pins = _pins()
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["ruff"], pins["ruff"])
        self.assertEqual(lock["mypy"], pins["mypy"])
        self.assertEqual(lock["pytest"], pins["pytest"])

    def test_repo_runtime_pins_match_the_lock(self) -> None:
        """Core's own gate toolchain must come from the same canonical owner.

        ``requirements-repo-runtime.txt`` provisions the interpreter that
        ``.l9/repo-workflow.json`` runs ruff and mypy through, and
        ``tools/check_toolchain_versions.py`` asserts that interpreter against
        ``toolchain-lock.json``. If this file could drift from the lock, the
        preflight would fail for a repository that had correctly installed
        what its own requirements declared — the pin files would disagree and
        the gate would blame the environment.

        Every other copy of these versions is already bound to the lock
        (installer pins, pre-commit rev, Biome schema). This closes the last
        one, so the lock is the single place a version is decided.
        """
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        runtime = dict(PIN_RE.findall(REPO_RUNTIME.read_text(encoding="utf-8")))
        self.assertEqual(
            {"ruff", "mypy"},
            set(runtime),
            "requirements-repo-runtime.txt must pin exactly ruff and mypy; "
            "the gate imports those two through `@python -m`",
        )
        for tool in ("ruff", "mypy"):
            with self.subTest(tool=tool):
                self.assertEqual(
                    lock[tool],
                    runtime[tool],
                    f"{tool} differs between toolchain-lock.json and "
                    "requirements-repo-runtime.txt; bump the lock and let "
                    "every other pin follow it",
                )

    def test_biome_lock_matches_preset_schema(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        schema = json.loads(BIOME.read_text(encoding="utf-8"))["$schema"]
        match = re.search(r"/schemas/([0-9.]+)/", schema)
        self.assertIsNotNone(match, f"biome schema unreadable: {schema}")
        self.assertEqual(lock["biome"], match.group(1))

    def test_precommit_rev_equals_ruff_pin(self) -> None:
        pins = _pins()
        text = PRECOMMIT.read_text(encoding="utf-8")
        match = re.search(r"(?m)^\s+rev:\s+(v[0-9.]+)\s*$", text)
        self.assertIsNotNone(match, "pre-commit ruff rev missing")
        self.assertEqual(match.group(1), f"v{pins['ruff']}")

    def test_root_file_is_include_only(self) -> None:
        text = ROOT_INCLUDE.read_text(encoding="utf-8")
        self.assertIn(
            "-r .github/actions/install-consumer-ci/requirements-consumer-ci.txt",
            text,
        )
        self.assertIsNone(PIN_RE.search(text))

    def test_core_owned_tools_are_exactly_the_lock_dependencies(self) -> None:
        """``core-owned-tools.json`` is the version-free list a Dependabot
        renderer ignores (lane 3). It must name every lock key that a consumer
        can declare as a dependency, and nothing the lock does not own.
        """
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        tools = json.loads(TOOLS.read_text(encoding="utf-8"))
        self.assertEqual("l9.core-owned-tools/v1", tools["schema"])
        self.assertEqual(
            ".github/actions/install-consumer-ci/toolchain-lock.json", tools["lock"]
        )
        excluded = set(tools["non_dependency_lock_keys"])
        self.assertEqual({"pip", "python"}, excluded)
        self.assertLessEqual(excluded, set(lock))
        names = [tool["name"] for tool in tools["tools"]]
        self.assertEqual(sorted(names), names, "keep tools sorted by name")
        self.assertEqual(set(lock) - excluded, set(names))

    def test_core_owned_tools_carry_identifiers_and_no_versions(self) -> None:
        tools = json.loads(TOOLS.read_text(encoding="utf-8"))
        ecosystems = {"npm", "pip", "pre-commit", "uv"}
        for tool in tools["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertEqual({"ecosystems", "name"}, set(tool))
                self.assertTrue(tool["ecosystems"])
                self.assertLessEqual(set(tool["ecosystems"]), ecosystems)
                for identifiers in tool["ecosystems"].values():
                    self.assertTrue(identifiers)
                    for identifier in identifiers:
                        self.assertNotRegex(identifier, r"[=<>~^]|@\d")
        self.assertNotRegex(TOOLS.read_text(encoding="utf-8"), r"\d+\.\d+\.\d+")

    def test_install_sh_rejects_unpinned_ruff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "install.sh").write_text(
                INSTALL.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (dest / "requirements-consumer-ci.txt").write_text(
                "ruff\nmypy==2.3.0\npytest==9.1.1\n", encoding="utf-8"
            )
            proc = subprocess.run(
                ["bash", str(dest / "install.sh")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("unpinned", proc.stderr)


if __name__ == "__main__":
    unittest.main()
