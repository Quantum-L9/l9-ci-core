"""Guard: unittest discover must collect every tests/**/test_*.py module."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"


def _is_testcase_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TestCase"
    if isinstance(node, ast.Attribute):
        return node.attr == "TestCase"
    return False


class UnittestCollectabilityTests(unittest.TestCase):
    def test_no_module_level_test_functions(self) -> None:
        missed: list[str] = []
        for path in sorted(TESTS.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            module_tests = [
                node.name
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            ]
            if module_tests:
                missed.append(f"{path.relative_to(ROOT)}: {', '.join(module_tests)}")
        self.assertEqual(missed, [], "unittest discover will skip these functions")
