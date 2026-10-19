"""Guard: unittest discover must collect every tests/**/test_*.py module."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


class CorpusMissingError(RuntimeError):
    """The scan found nothing to inspect, so it cannot vouch for anything."""


def _is_testcase_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TestCase"
    if isinstance(node, ast.Attribute):
        return node.attr == "TestCase"
    return False


def _module_level_test_functions(tests_root: Path) -> list[str]:
    """Return every module-level ``test_*`` function under ``tests_root``.

    ``unittest discover`` collects only ``TestCase`` methods, so a sync or async
    function at module scope silently never runs. A missing or empty corpus
    raises instead of returning ``[]``: a guard that inspected zero files must
    not report green.
    """
    if not tests_root.is_dir():
        raise CorpusMissingError(f"tests directory not found: {tests_root}")
    modules = sorted(tests_root.rglob("test_*.py"))
    if not modules:
        raise CorpusMissingError(f"no test_*.py modules under {tests_root}")
    missed: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_tests = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        if module_tests:
            missed.append(
                f"{path.relative_to(tests_root.parent)}: {', '.join(module_tests)}"
            )
    return missed


class UnittestCollectabilityTests(unittest.TestCase):
    def test_no_module_level_test_functions(self) -> None:
        missed = _module_level_test_functions(TESTS)
        self.assertEqual(missed, [], "unittest discover will skip these functions")

    def test_scan_targets_this_repository_tests_tree(self) -> None:
        here = Path(__file__).resolve()
        self.assertEqual(TESTS, here.parent)
        self.assertIn(here, set(TESTS.rglob("test_*.py")))


class ModuleLevelScanWitnessTests(unittest.TestCase):
    def _write(self, root: Path, name: str, source: str) -> None:
        path = root / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_missing_tests_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CorpusMissingError):
                _module_level_test_functions(Path(tmp) / "tests")

    def test_empty_tests_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tests" / "pkg").mkdir(parents=True)
            with self.assertRaises(CorpusMissingError):
                _module_level_test_functions(Path(tmp) / "tests")

    def test_sync_and_async_module_level_tests_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "pkg/test_loose.py",
                "def test_sync():\n    pass\n\n\nasync def test_async():\n    pass\n",
            )
            self.assertEqual(
                _module_level_test_functions(root / "tests"),
                ["tests/pkg/test_loose.py: test_sync, test_async"],
            )

    def test_testcase_methods_are_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "test_methods.py",
                "import unittest\n\n\n"
                "class Cases(unittest.TestCase):\n"
                "    def test_sync(self):\n        pass\n\n"
                "    async def test_async(self):\n        pass\n",
            )
            self.assertEqual(_module_level_test_functions(root / "tests"), [])
