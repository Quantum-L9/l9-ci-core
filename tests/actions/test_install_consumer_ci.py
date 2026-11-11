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
# Every Python package a consumer installs from Core instead of declaring it.
INSTALLER_TOOLS = ("pip", "pre-commit", "ruff", "mypy", "pytest")
INSTALLER_RE = re.compile(r"^(pip|pre-commit|ruff|mypy|pytest)==([0-9][^\s]+)$", re.M)


def _pins() -> dict[str, str]:
    text = PINS.read_text(encoding="utf-8")
    found = dict(INSTALLER_RE.findall(text))
    if set(found) != set(INSTALLER_TOOLS):
        raise AssertionError(f"pin file missing exact toolchain lines: {found}")
    return found


class InstallConsumerCiTests(unittest.TestCase):
    def test_pin_file_and_lock_json_match(self) -> None:
        pins = _pins()
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        for tool in INSTALLER_TOOLS:
            with self.subTest(tool=tool):
                self.assertEqual(lock[tool], pins[tool])

    def test_pin_file_holds_only_exact_installer_lines(self) -> None:
        """A consumer declares none of these, so the installer is its only
        source: every line is an exact pin of a Core-owned pip tool."""
        lines = [
            line.strip()
            for line in PINS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(len(INSTALLER_TOOLS), len(lines), lines)
        for line in lines:
            self.assertRegex(line, INSTALLER_RE)

    def test_installer_covers_every_core_owned_pip_tool_but_semgrep(self) -> None:
        """Every Core-owned tool a consumer could take from PyPI is installable
        from the pin file. semgrep is the exception: it installs through the
        hash-locked ``install-semgrep`` action."""
        tools = json.loads(TOOLS.read_text(encoding="utf-8"))
        pip_tools = {t["name"] for t in tools["tools"] if "pip" in t["ecosystems"]}
        self.assertEqual(pip_tools - {"semgrep"}, set(INSTALLER_TOOLS))

    def test_repo_runtime_pins_match_the_lock(self) -> None:
        """Core's own gate toolchain must come from the same canonical owner.

        ``requirements-repo-runtime.txt`` provisions the interpreter that
        Core's ``Repo.mk`` ``repo-check`` leaf runs ruff and mypy through, and
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
            "the gate imports those two through `$(PYTHON) -m`",
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
        self.assertEqual({"python"}, excluded)
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
                "pip==26.2.1\npre-commit==4.4.0\nruff\nmypy==2.3.0\npytest==9.1.1\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", str(dest / "install.sh")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("unpinned", proc.stderr)

    def test_install_sh_requires_every_installer_pin(self) -> None:
        """A consumer declares no Core tool, so a pin file that drops one would
        silently leave the consumer without it. The installer refuses."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "install.sh").write_text(
                INSTALL.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (dest / "requirements-consumer-ci.txt").write_text(
                "pip==26.2.1\nruff==0.16.1\nmypy==2.3.0\npytest==9.1.1\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", str(dest / "install.sh")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("missing pre-commit== pin", proc.stderr)


if __name__ == "__main__":
    unittest.main()
