from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PATTERNS = (
    "import semgrep",
    "from semgrep",
    "class Finding(",
    "class EvidenceRecord(",
    "class ProviderFailure(",
    "class FindingBundle(",
    "tree_sitter",
    "tree-sitter",
)
ALLOWED_SUFFIXES = {".py", ".sh", ".yml", ".yaml"}
EXCLUDED = {
    Path("tests/architecture/test_forbidden_sdk_ownership.py"),
    Path(".l9/architecture.yaml"),
    Path(".l9/ownership.yaml"),
    Path(".l9/repo-spec.yaml"),
}


class ForbiddenOwnershipTests(unittest.TestCase):
    def test_product_files_do_not_implement_sdk_responsibilities(self) -> None:
        violations: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in ALLOWED_SUFFIXES:
                continue
            relative = path.relative_to(ROOT)
            if (
                relative in EXCLUDED
                or ".git" in relative.parts
                or relative.parts[0] == "tests"
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.lower() in text:
                    violations.append(f"{relative}:{pattern}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
