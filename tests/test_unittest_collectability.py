"""Guard: tests/**/test_*.py must not hide tests that unittest discover skips.

The guard covers three silent omissions, and only these: module-level
``test_*`` functions, test modules under a directory that is not a package,
and ``async def test_*`` methods on a class that is not an
``IsolatedAsyncioTestCase``. Other discovery gaps, such as a module name that
is not a valid identifier, are outside what this guard claims.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
ASYNC_TESTCASE = "IsolatedAsyncioTestCase"


class CorpusMissingError(RuntimeError):
    """The scan found nothing to inspect, so it cannot vouch for anything."""


def _non_package_directory(tests_root: Path, path: Path) -> Path | None:
    """Return the first directory between ``tests_root`` and ``path`` without
    an ``__init__.py``, or ``None`` when every directory is a package."""
    directory = tests_root
    for part in path.parent.relative_to(tests_root).parts:
        directory = directory / part
        if not (directory / "__init__.py").is_file():
            return directory
    return None


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _uncollectable_tests(tests_root: Path) -> list[str]:
    """Return every test under ``tests_root`` that unittest discover skips.

    ``unittest discover`` collects only ``TestCase`` methods, so a sync or async
    function at module scope silently never runs. It imports a module only when
    every directory between the start directory and the module is a package,
    so a ``TestCase`` under a directory without ``__init__.py`` is never
    collected. A plain ``TestCase`` calls an ``async def test_*`` method without
    awaiting it, so the body never runs and the test passes. A missing or empty
    corpus raises instead of returning ``[]``: a guard that inspected zero files
    must not report green.
    """
    if not tests_root.is_dir():
        raise CorpusMissingError(f"tests directory not found: {tests_root}")
    modules = sorted(tests_root.rglob("test_*.py"))
    if not modules:
        raise CorpusMissingError(f"no test_*.py modules under {tests_root}")
    missed: list[str] = []
    for path in modules:
        label = path.relative_to(tests_root.parent)
        directory = _non_package_directory(tests_root, path)
        if directory is not None:
            missed.append(
                f"{label}: directory {directory.relative_to(tests_root.parent)} "
                "is not a package"
            )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_tests = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        if module_tests:
            missed.append(f"{label}: {', '.join(module_tests)}")
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(_base_name(base) == ASYNC_TESTCASE for base in node.bases):
                continue
            async_tests = [
                f"{node.name}.{method.name}"
                for method in node.body
                if isinstance(method, ast.AsyncFunctionDef)
                and method.name.startswith("test_")
            ]
            if async_tests:
                missed.append(
                    f"{label}: {', '.join(async_tests)} "
                    f"(async test method outside {ASYNC_TESTCASE})"
                )
    return missed


class UnittestCollectabilityTests(unittest.TestCase):
    def test_no_uncollectable_tests(self) -> None:
        missed = _uncollectable_tests(TESTS)
        self.assertEqual(missed, [], "unittest discover will skip these tests")

    def test_scan_targets_this_repository_tests_tree(self) -> None:
        here = Path(__file__).resolve()
        self.assertEqual(TESTS, here.parent)
        self.assertIn(here, set(TESTS.rglob("test_*.py")))


class UncollectableScanWitnessTests(unittest.TestCase):
    def _write(
        self, root: Path, name: str, source: str, *, packages: bool = True
    ) -> None:
        """Write ``tests/<name>``; intermediate directories are packages unless
        ``packages`` is false, matching what unittest discover can import."""
        tests = root / "tests"
        path = tests / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if packages:
            directory = tests
            for part in path.parent.relative_to(tests).parts:
                directory = directory / part
                (directory / "__init__.py").touch()
        path.write_text(source, encoding="utf-8")

    def test_missing_tests_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CorpusMissingError):
                _uncollectable_tests(Path(tmp) / "tests")

    def test_empty_tests_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tests" / "pkg").mkdir(parents=True)
            with self.assertRaises(CorpusMissingError):
                _uncollectable_tests(Path(tmp) / "tests")

    def test_sync_and_async_module_level_tests_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "pkg/test_loose.py",
                "def test_sync():\n    pass\n\n\nasync def test_async():\n    pass\n",
            )
            self.assertEqual(
                _uncollectable_tests(root / "tests"),
                ["tests/pkg/test_loose.py: test_sync, test_async"],
            )

    def test_sync_testcase_methods_are_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "test_methods.py",
                "import unittest\n\n\n"
                "class Cases(unittest.TestCase):\n"
                "    def test_sync(self):\n        pass\n",
            )
            self.assertEqual(_uncollectable_tests(root / "tests"), [])

    def test_async_method_on_plain_testcase_is_reported(self) -> None:
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
            self.assertEqual(
                _uncollectable_tests(root / "tests"),
                [
                    "tests/test_methods.py: Cases.test_async "
                    "(async test method outside IsolatedAsyncioTestCase)"
                ],
            )

    def test_async_method_on_isolated_asyncio_testcase_is_not_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "test_async.py",
                "import unittest\n"
                "from unittest import IsolatedAsyncioTestCase\n\n\n"
                "class Qualified(unittest.IsolatedAsyncioTestCase):\n"
                "    async def test_async(self):\n        pass\n\n\n"
                "class Imported(IsolatedAsyncioTestCase):\n"
                "    async def test_async(self):\n        pass\n",
            )
            self.assertEqual(_uncollectable_tests(root / "tests"), [])

    def test_module_under_non_package_directory_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "nested/test_nested.py",
                "import unittest\n\n\n"
                "class Nested(unittest.TestCase):\n"
                "    def test_nested(self):\n        pass\n",
                packages=False,
            )
            self.assertEqual(
                _uncollectable_tests(root / "tests"),
                [
                    "tests/nested/test_nested.py: directory tests/nested "
                    "is not a package"
                ],
            )

    def test_module_under_package_directory_is_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "nested/deeper/test_nested.py",
                "import unittest\n\n\n"
                "class Nested(unittest.TestCase):\n"
                "    def test_nested(self):\n        pass\n",
            )
            self.assertEqual(_uncollectable_tests(root / "tests"), [])
