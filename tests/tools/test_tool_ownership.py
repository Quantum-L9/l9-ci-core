"""Core's tool-ownership manifest (``l9.tool-ownership/v1``, AGENTS.md §26).

The manifest is derived; these tests prove it is current, that every fact it
asserts is grounded in Core's own files, and that its inputs agree with one
another (one owner per tool, one tool per stamped config, every step real).
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import render_tool_ownership  # noqa: E402
from l9_repo.stamp import PROFILE_FILES, PROFILES  # noqa: E402
from l9_repo.tool_ownership import (  # noqa: E402
    MANIFEST_PATH,
    NON_TOOL_FILES,
    NOT_OWNED,
    TOOLS,
    build_manifest,
    render_manifest,
)

ACTION = ROOT / ".github" / "actions" / "install-consumer-ci"
LOCK = ACTION / "toolchain-lock.json"
IDENTITY = ACTION / "core-owned-tools.json"
WORKFLOWS = ROOT / ".github" / "workflows"


def _steps(workflow: str, job: str) -> list[str]:
    document = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return [step.get("name", "") for step in document["jobs"][job].get("steps", [])]


class ManifestCurrencyTests(unittest.TestCase):
    def test_checked_in_manifest_is_the_rendering(self) -> None:
        self.assertEqual(
            render_manifest(),
            MANIFEST_PATH.read_bytes(),
            "run python3 tools/render_tool_ownership.py",
        )

    def test_check_mode_passes_and_does_not_write(self) -> None:
        before = MANIFEST_PATH.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, render_tool_ownership.main(["--check"]))
        self.assertEqual(before, MANIFEST_PATH.read_bytes())

    def test_manifest_records_its_inputs(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual("l9.tool-ownership/v1", manifest["schema"])
        self.assertTrue(manifest["derived"])
        paths = [source["path"] for source in manifest["sources"]]
        self.assertEqual(
            [
                ".github/actions/install-consumer-ci/toolchain-lock.json",
                ".github/actions/install-consumer-ci/core-owned-tools.json",
                "tools/l9_repo/stamp.py",
                "tools/l9_repo/tool_ownership.py",
            ],
            paths,
        )
        for source in manifest["sources"]:
            self.assertRegex(source["sha256"], r"^sha256:[0-9a-f]{64}$")


class ManifestGroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = build_manifest()
        self.lock = json.loads(LOCK.read_text(encoding="utf-8"))

    def test_one_entry_per_lock_key_with_the_lock_version(self) -> None:
        tools = {tool["name"]: tool for tool in self.manifest["tools"]}
        self.assertEqual(set(self.lock), set(tools))
        for name, version in self.lock.items():
            with self.subTest(tool=name):
                self.assertEqual(version, tools[name]["version"])

    def test_identity_list_names_the_same_tools(self) -> None:
        identity = json.loads(IDENTITY.read_text(encoding="utf-8"))
        names = {tool["name"] for tool in identity["tools"]}
        names |= set(identity["non_dependency_lock_keys"])
        self.assertEqual(set(TOOLS), names)

    def test_every_stamped_file_has_exactly_one_owner(self) -> None:
        owners: dict[str, list[str]] = {}
        for tool in self.manifest["tools"]:
            for path in tool["configs"]:
                owners.setdefault(path, []).append(tool["name"])
        stamped = {path for files in PROFILE_FILES.values() for path in files}
        self.assertEqual(stamped - set(NON_TOOL_FILES), set(owners))
        for path, names in owners.items():
            with self.subTest(path=path):
                self.assertEqual(1, len(names), names)

    def test_profiles_are_the_ones_that_stamp_the_config(self) -> None:
        for tool in self.manifest["tools"]:
            for path in tool["configs"]:
                with self.subTest(tool=tool["name"], path=path):
                    carrying = [p for p in PROFILES if path in PROFILE_FILES[p]]
                    self.assertTrue(set(carrying) <= set(tool["profiles"]))

    def test_every_run_names_a_real_workflow_step(self) -> None:
        runs = [
            (tool["name"], run)
            for tool in self.manifest["tools"]
            for run in tool["runs"]
        ]
        runs.append(("consumer", self.manifest["consumer_execution"]))
        self.assertTrue(runs)
        for name, run in runs:
            with self.subTest(tool=name, step=run["step"]):
                self.assertIn(run["step"], _steps(run["workflow"], run["job"]))

    def test_every_owned_tool_is_run_or_stamped(self) -> None:
        """'Do not add a tool nothing runs': an owned tool is executed by a
        Core workflow step or configured by a stamped profile."""
        for tool in self.manifest["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["runs"] or tool["profiles"])

    def test_mypy_is_the_only_type_checker(self) -> None:
        """Strict mypy is the single organization CI type gate (AGENTS.md §26);
        Pyright is a gate nowhere."""
        self.assertEqual("mypy", self.manifest["languages"]["python"]["type_checker"])
        self.assertNotIn("pyright", {tool["name"] for tool in self.manifest["tools"]})
        self.assertIn("pyright", self.manifest["not_owned"])
        stamped = {path for files in PROFILE_FILES.values() for path in files}
        self.assertNotIn("pyrightconfig.json", stamped)

    def test_languages_have_one_formatter_and_unowned_ones_are_disjoint(self) -> None:
        languages = self.manifest["languages"]
        self.assertEqual("ruff", languages["python"]["formatter"])
        for language in ("javascript", "typescript", "json"):
            self.assertEqual("biome", languages[language]["formatter"])
        self.assertEqual({"jsonc", "markdown"}, set(self.manifest["unowned_languages"]))
        self.assertFalse(set(languages) & set(self.manifest["unowned_languages"]))

    def test_not_owned_tools_are_absent_from_the_lock(self) -> None:
        self.assertFalse(set(NOT_OWNED) & set(self.lock))
        for name, record in NOT_OWNED.items():
            with self.subTest(tool=name):
                self.assertEqual({"decision", "evidence", "adopt_when"}, set(record))


if __name__ == "__main__":
    unittest.main()
