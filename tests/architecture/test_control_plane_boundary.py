from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PATH_PARTS = {
    "parsers",
    "scanners",
    "providers",
    "contracts",
    "schemas",
    "identity",
    "normalization",
    "ast",
    "tree_sitter",
    "tree-sitter",
    "repository_graph",
}
EXCLUDED_ROOTS = {".git", ".l9", "tests"}


class ControlPlaneBoundaryTests(unittest.TestCase):
    def test_forbidden_sdk_implementation_directories_do_not_exist(self) -> None:
        violations: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_dir():
                continue
            relative = path.relative_to(ROOT)
            if not relative.parts:
                continue
            if relative.parts[0] in EXCLUDED_ROOTS:
                continue
            if any(part.lower() in FORBIDDEN_PATH_PARTS for part in relative.parts):
                violations.append(relative.as_posix())
        self.assertEqual(
            [],
            violations,
            "Core contains directories reserved for SDK-owned implementation",
        )

    def test_core_has_no_python_product_package(self) -> None:
        packages = []
        for init_file in ROOT.glob("*/__init__.py"):
            if init_file.parent.name not in {"tests"}:
                packages.append(init_file.parent.name)
        self.assertEqual(
            [],
            packages,
            "Phase 1 Core must not introduce a Python product package",
        )


if __name__ == "__main__":
    unittest.main()
