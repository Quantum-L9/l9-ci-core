from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "check_workflow_integrity.py"
spec = importlib.util.spec_from_file_location("check_workflow_integrity", MODULE_PATH)
assert spec is not None and spec.loader is not None
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)


class CheckFileTests(unittest.TestCase):
    def _check(self, body: str) -> list[str]:
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(body)
            path = Path(handle.name)
        try:
            return integrity.check_file(path)
        finally:
            path.unlink()

    def test_clean_workflow_has_no_violations(self) -> None:
        self.assertEqual(
            [], self._check("jobs:\n  a:\n    steps:\n      - run: pytest\n")
        )

    def test_continue_on_error_is_flagged(self) -> None:
        violations = self._check("    continue-on-error: true\n")
        self.assertEqual(1, len(violations))
        self.assertIn("continue-on-error", violations[0])

    def test_fail_open_shell_suffix_is_flagged(self) -> None:
        self.assertTrue(
            any("fail-open" in v for v in self._check("      run: make ci || true\n"))
        )
        self.assertTrue(
            any(
                "fail-open" in v for v in self._check("      run: do-thing || exit 0\n")
            )
        )

    def test_benign_grep_guard_is_exempt(self) -> None:
        self.assertEqual([], self._check("      run: grep -q foo file || true\n"))

    def test_command_substitution_is_exempt(self) -> None:
        self.assertEqual([], self._check("      run: x=$(cmd || true)\n"))

    def test_forbidden_skip_override_is_flagged(self) -> None:
        violations = self._check("      run: SKIP=ruff,mypy pre-commit run\n")
        self.assertTrue(any("forbidden SKIP" in v for v in violations))
        self.assertIn("ruff", violations[0])
        self.assertIn("mypy", violations[0])

    def test_allowlisted_gitleaks_skip_is_permitted(self) -> None:
        self.assertEqual([], self._check("      run: SKIP=gitleaks pre-commit run\n"))

    def test_malformed_action_reference_is_flagged(self) -> None:
        body = "      - uses: actions/checkout@" + "a" * 40 + "v4\n"
        self.assertTrue(
            any("malformed action reference" in v for v in self._check(body))
        )

    def test_comment_only_fail_open_is_ignored(self) -> None:
        self.assertEqual(
            [], self._check("      run: make ci  # never use || true here\n")
        )


if __name__ == "__main__":
    unittest.main()
