"""Advisory consumer toolchain-alignment report.

Fixtures are real excerpts of Cursor-Governance and the cited gate_sdk shapes,
stored with a ``.fixture`` suffix so no tool treats them as live configuration;
each test copies them into a temporary workspace under their real names.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "check-toolchain-alignment"
SCRIPT = ACTION / "align.py"
FIXTURES = ROOT / "tests" / "fixtures" / "toolchain-alignment"
REPORT = Path(".l9") / "runtime" / "toolchain-alignment.json"
LOCK = json.loads(
    (
        ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"
    ).read_text(encoding="utf-8")
)


def load_module():
    spec = importlib.util.spec_from_file_location("align", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


align = load_module()


class WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.summary = self.tmp / "summary.md"

    def stage(self, fixture: str) -> None:
        for source in sorted((FIXTURES / fixture).iterdir()):
            name = source.name.removesuffix(".fixture")
            shutil.copy(source, self.workspace / name)

    def write(self, name: str, text: str) -> None:
        (self.workspace / name).write_text(text, encoding="utf-8")

    def run_action(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["GITHUB_WORKSPACE"] = str(self.workspace)
        env["GITHUB_STEP_SUMMARY"] = str(self.summary)
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def report(self) -> dict[str, object]:
        return json.loads((self.workspace / REPORT).read_text(encoding="utf-8"))

    def findings(self) -> set[tuple[str, int, str, str]]:
        return {
            (item["path"], item["line"], item["tool"], item["class"])
            for item in self.report()["findings"]  # type: ignore[union-attr]
        }


class FixtureTests(WorkspaceCase):
    def test_gate_sdk_shape_reports_ranges_and_pre_commit_drift(self) -> None:
        self.stage("gate-sdk-shape")
        proc = self.run_action()
        self.assertEqual(0, proc.returncode, proc.stderr)
        found = self.findings()
        self.assertIn(("pyproject.toml", 31, "ruff", "range_admits_lock"), found)
        self.assertIn(("pyproject.toml", 27, "mypy", "range_admits_lock"), found)
        self.assertIn((".pre-commit-config.yaml", 14, "ruff", "exact_drift"), found)
        self.assertIn((".pre-commit-config.yaml", 21, "mypy", "exact_drift"), found)
        self.assertEqual("drift", self.report()["status"])
        self.assertIn(
            "::warning file=.pre-commit-config.yaml,line=14::ruff v0.15.20",
            proc.stdout,
        )

    def test_cursor_governance_excerpt_reports_its_pre_commit_drift(self) -> None:
        self.stage("cursor-governance")
        proc = self.run_action()
        self.assertEqual(0, proc.returncode, proc.stderr)
        report = self.report()
        drift = {
            (item["path"], item["tool"], item["declared"])
            for item in report["findings"]  # type: ignore[union-attr]
            if item["class"] == "exact_drift"
        }
        self.assertEqual(
            {
                ("pyproject.toml", "pre-commit", "==4.6.2"),
                ("requirements.txt", "pre-commit", "==4.6.2"),
                ("uv.lock", "pre-commit", "4.6.2"),
            },
            drift,
        )
        matches = {
            (item["path"], item["tool"])
            for item in report["findings"]  # type: ignore[union-attr]
            if item["class"] == "exact_match"
        }
        self.assertIn((".pre-commit-config.yaml", "ruff"), matches)
        self.assertIn(("biome.json", "biome"), matches)
        self.assertIn(("uv.lock", "ruff"), matches)
        # pytest-cov is not pytest; normalised names must not alias.
        tools = {item["tool"] for item in report["findings"]}  # type: ignore[union-attr]
        self.assertNotIn("pytest-cov", tools)

    def test_empty_workspace_is_not_applicable(self) -> None:
        proc = self.run_action()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("not_applicable", self.report()["status"])
        self.assertEqual([], self.report()["findings"])

    def test_report_is_deterministic(self) -> None:
        self.stage("cursor-governance")
        self.run_action()
        first = (self.workspace / REPORT).read_bytes()
        self.run_action()
        self.assertEqual(first, (self.workspace / REPORT).read_bytes())
        self.assertNotIn(str(self.workspace), first.decode("utf-8"))


class ClassificationTests(WorkspaceCase):
    def test_every_class_is_reachable_with_its_line(self) -> None:
        ruff = LOCK["ruff"]
        self.write(
            "requirements-dev.txt",
            "\n".join(
                [
                    f"ruff=={ruff}",  # 1 exact_match
                    "mypy==0.0.1 \\",  # 2 exact_drift
                    "    --hash=sha256:" + "0" * 64,
                    "pytest>=1.0,<99",  # 4 range_admits_lock
                    "semgrep<1.0",  # 5 range_excludes_lock
                    "pre-commit",  # 6 unpinned
                    "ruff @ https://example.invalid/ruff.whl",  # 7 unparseable
                    "requests==2.0.0",  # not a Core-owned tool
                ]
            )
            + "\n",
        )
        proc = self.run_action()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(
            {
                ("requirements-dev.txt", 1, "ruff", "exact_match"),
                ("requirements-dev.txt", 2, "mypy", "exact_drift"),
                ("requirements-dev.txt", 4, "pytest", "range_admits_lock"),
                ("requirements-dev.txt", 5, "semgrep", "range_excludes_lock"),
                ("requirements-dev.txt", 6, "pre-commit", "unpinned"),
                ("requirements-dev.txt", 7, "ruff", "unparseable"),
            },
            self.findings(),
        )
        self.assertIn("::notice file=requirements-dev.txt,line=6::", proc.stdout)

    def test_pep440_operators(self) -> None:
        lock = "1.171.0"
        cases = {
            "==1.171.0": "exact_match",
            "==1.170.0": "exact_drift",
            ">=1.100,<2": "range_admits_lock",
            ">=1.100.0,<2.0.0": "range_admits_lock",
            "~=1.171": "range_admits_lock",
            "~=1.170.0": "range_excludes_lock",
            "==1.171.*": "range_admits_lock",
            "!=1.171.0": "range_excludes_lock",
            ">2": "range_excludes_lock",
            "": "unpinned",
            "==latest": "unparseable",
        }
        for spec, expected in cases.items():
            with self.subTest(spec=spec):
                self.assertEqual(expected, align.classify_pep440(spec, lock)[0])

    def test_npm_ranges(self) -> None:
        lock = "2.5.8"
        cases = {
            "2.5.8": "exact_match",
            "=2.5.5": "exact_drift",
            "^2.5.5": "range_admits_lock",
            "^2.6.0": "range_excludes_lock",
            "~2.5.0": "range_admits_lock",
            "~2.4.0": "range_excludes_lock",
            ">=2.0.0 <3.0.0": "range_admits_lock",
            "*": "unpinned",
            "latest": "unpinned",
            "^2.5.5 || ^3": "unparseable",
        }
        for spec, expected in cases.items():
            with self.subTest(spec=spec):
                self.assertEqual(expected, align.classify_npm(spec, lock))

    def test_biome_schema_and_package_json(self) -> None:
        self.write(
            "biome.jsonc",
            '// comment\n{\n  "$schema": "https://biomejs.dev/schemas/2.5.5/schema.json"\n}\n',
        )
        self.write(
            "package.json",
            '{\n  "devDependencies": {\n    "@biomejs/biome": "^2.5.5"\n  }\n}\n',
        )
        self.run_action()
        self.assertEqual(
            {
                ("biome.jsonc", 3, "biome", "exact_drift"),
                ("package.json", 3, "biome", "range_admits_lock"),
            },
            self.findings(),
        )

    def test_pyproject_tables_and_include_groups(self) -> None:
        self.write(
            "pyproject.toml",
            "\n".join(
                [
                    "[project]",
                    'name = "x"',
                    'version = "0"',
                    "",
                    "[dependency-groups]",
                    'lint = ["ruff==0.1.0"]',
                    'dev = [{include-group = "lint"}, "pytest>=1"]',
                    "",
                    "[tool.uv]",
                    'dev-dependencies = ["mypy<1"]',
                ]
            )
            + "\n",
        )
        self.run_action()
        self.assertEqual(
            {
                ("pyproject.toml", 6, "ruff", "exact_drift"),
                ("pyproject.toml", 7, "pytest", "range_admits_lock"),
                ("pyproject.toml", 10, "mypy", "range_excludes_lock"),
            },
            self.findings(),
        )


class MalformedConsumerDataTests(WorkspaceCase):
    """Malformed consumer files are findings; the advisory job never crashes."""

    def assert_unparseable(self, path: str) -> None:
        proc = self.run_action()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        classes = {
            (item["path"], item["class"])
            for item in self.report()["findings"]  # type: ignore[union-attr]
        }
        self.assertIn((path, "unparseable"), classes)

    def test_pyproject_with_non_table_sections(self) -> None:
        for text in (
            'project = "broken"\n',
            "tool = 1\n",
            '[project]\noptional-dependencies = "x"\n',
            "[tool]\nuv = []\n",
            '[dependency-groups]\ndev = "ruff==0.1.0"\n',
            "dependency-groups = [1]\n",
        ):
            with self.subTest(text=text):
                self.write("pyproject.toml", text)
                self.assert_unparseable("pyproject.toml")

    def test_malformed_tables_do_not_hide_valid_declarations(self) -> None:
        self.write(
            "pyproject.toml",
            'tool = 1\n[dependency-groups]\nlint = ["ruff==0.1.0"]\n',
        )
        self.run_action()
        self.assertEqual(
            {
                ("pyproject.toml", 1, "*", "unparseable"),
                ("pyproject.toml", 3, "ruff", "exact_drift"),
            },
            self.findings(),
        )

    def test_package_json_that_is_not_an_object(self) -> None:
        for text in ("[]\n", '"x"\n', "{not json\n"):
            with self.subTest(text=text):
                self.write("package.json", text)
                self.assert_unparseable("package.json")

    def test_non_utf8_files(self) -> None:
        for name in (
            "requirements.txt",
            "uv.lock",
            ".pre-commit-config.yaml",
            "biome.json",
            "pyproject.toml",
            "package.json",
        ):
            with self.subTest(name=name):
                (self.workspace / name).write_bytes(b"ruff==\xff\xfe\n")
                self.assert_unparseable(name)
                (self.workspace / name).unlink()


class ContainmentTests(WorkspaceCase):
    def test_symlink_out_of_the_workspace_is_never_read(self) -> None:
        outside = self.tmp / "outside.txt"
        outside.write_text("ruff==0.0.1\n", encoding="utf-8")
        (self.workspace / "requirements.txt").symlink_to(outside)
        (self.workspace / "pyproject.toml").symlink_to(outside)
        proc = self.run_action()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([], self.report()["findings"])

    def test_symlinked_report_directory_cannot_write_outside(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.workspace / ".l9").symlink_to(outside, target_is_directory=True)
        proc = self.run_action()
        self.assertEqual(2, proc.returncode)
        self.assertEqual([], sorted(outside.rglob("*")))

    def test_output_outside_the_workspace_is_refused(self) -> None:
        proc = self.run_action("--output", "../escape.json")
        self.assertEqual(2, proc.returncode)
        self.assertFalse((self.tmp / "escape.json").exists())

    def test_unreadable_core_lock_is_a_contract_failure(self) -> None:
        copy = self.tmp / "core" / "actions"
        shutil.copytree(ACTION, copy / "check-toolchain-alignment")
        (copy / "install-consumer-ci").mkdir()
        env = dict(os.environ)
        env["GITHUB_WORKSPACE"] = str(self.workspace)
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(copy / "check-toolchain-alignment" / "align.py"),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(3, proc.returncode, proc.stderr)
        self.assertIn("unreadable", proc.stderr)


class ActionContractTests(unittest.TestCase):
    def test_action_is_a_local_stdlib_leaf(self) -> None:
        action = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))
        self.assertEqual("composite", action["runs"]["using"])
        for step in action["runs"]["steps"]:
            self.assertNotIn("uses", step)
        source = SCRIPT.read_text(encoding="utf-8")
        for module in ("yaml", "packaging", "requests", "toml\n"):
            with self.subTest(module=module):
                self.assertNotIn(f"import {module}", source)

    def test_core_data_is_read_from_the_same_pinned_revision(self) -> None:
        self.assertEqual(
            ROOT
            / ".github"
            / "actions"
            / "install-consumer-ci"
            / "toolchain-lock.json",
            align.LOCK_FILE,
        )
        self.assertTrue(align.TOOLS_FILE.is_file())


if __name__ == "__main__":
    unittest.main()
