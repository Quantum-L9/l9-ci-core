from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "check_workflow_integrity.py"
spec = importlib.util.spec_from_file_location("check_workflow_integrity", MODULE_PATH)
assert spec is not None and spec.loader is not None
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)


class CheckFileTests(unittest.TestCase):
    def _check(self, body: str, *, composite: bool = False) -> list[str]:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(body)
            path = Path(handle.name)
        try:
            return integrity.check_file(path, composite=composite)
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

    def test_blocking_mypy_step_is_clean(self) -> None:
        # A required, blocking mypy invocation (no fail-open suffix) must pass:
        # a non-zero mypy exit propagates and fails the job.
        self.assertEqual([], self._check('      run: mypy "$SOURCE_DIR"\n'))

    def test_fail_open_mypy_true_suffix_is_flagged(self) -> None:
        # `mypy ... || true` / `|| exit 0` silently masks type debt — the
        # explicit-blocking discipline requires this be caught.
        self.assertTrue(
            any(
                "fail-open" in v
                for v in self._check('      run: mypy "$SOURCE_DIR" || true\n')
            )
        )
        self.assertTrue(
            any(
                "fail-open" in v
                for v in self._check('      run: mypy "$SOURCE_DIR" || exit 0\n')
            )
        )

    def test_advisory_mypy_notice_branch_is_permitted(self) -> None:
        # The explicit advisory branch (guarded by mypy-required=false) surfaces
        # findings as a notice rather than `|| true`; a `|| echo ::notice::` is
        # not a `|| true` fail-open and must not be flagged.
        self.assertEqual(
            [],
            self._check(
                '      run: mypy "$SOURCE_DIR" || echo "::notice::mypy advisory"\n'
            ),
        )

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

    def test_remote_action_requires_a_full_lowercase_sha(self) -> None:
        for revision in ("main", "v4", "a" * 39, "A" * 40):
            with self.subTest(revision=revision):
                violations = self._check(f"      - uses: actions/checkout@{revision}\n")
                self.assertTrue(any("full lowercase" in v for v in violations))

    def test_remote_action_with_full_sha_is_clean(self) -> None:
        self.assertEqual(
            [], self._check("      - uses: actions/checkout@" + "a" * 40 + "\n")
        )

    def test_quoted_remote_action_with_full_sha_is_clean(self) -> None:
        self.assertEqual(
            [],
            self._check('      - uses: "actions/checkout@' + "a" * 40 + '"\n'),
        )

    def test_local_action_and_workflow_edges_are_clean(self) -> None:
        self.assertEqual([], self._check("      - uses: ./.github/actions/local\n"))
        self.assertEqual([], self._check("    uses: ./.github/workflows/local.yml\n"))

    def test_composite_action_may_not_nest_a_core_action(self) -> None:
        body = (
            "      - uses: Quantum-L9/l9-ci-core/.github/actions/invoke-sdk@"
            + "a" * 40
            + "\n"
        )
        violations = self._check(body, composite=True)
        self.assertTrue(any("local leaf" in violation for violation in violations))

    def test_workflow_may_reference_a_sha_pinned_core_action(self) -> None:
        body = (
            "      - uses: Quantum-L9/l9-ci-core/.github/actions/invoke-sdk@"
            + "a" * 40
            + "\n"
        )
        self.assertEqual([], self._check(body))

    def test_comment_only_fail_open_is_ignored(self) -> None:
        self.assertEqual(
            [], self._check("      run: make ci  # never use || true here\n")
        )

    def test_main_recursively_checks_yaml_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / "workflows"
            actions = root / "actions" / "nested"
            workflows.mkdir()
            actions.mkdir(parents=True)
            (workflows / "clean.yml").write_text("jobs: {}\n", encoding="utf-8")
            (actions / "action.yaml").write_text(
                "runs:\n  using: composite\n  steps:\n"
                "    - uses: Quantum-L9/l9-ci-core/.github/actions/nested@"
                + "a" * 40
                + "\n",
                encoding="utf-8",
            )
            with patch.object(
                sys,
                "argv",
                [
                    "check_workflow_integrity.py",
                    "--workflows-dir",
                    str(workflows),
                    "--actions-dir",
                    str(root / "actions"),
                ],
            ):
                self.assertEqual(1, integrity.main())

    def test_main_allows_a_repository_without_composite_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / "workflows"
            workflows.mkdir()
            (workflows / "clean.yml").write_text("jobs: {}\n", encoding="utf-8")
            with patch.object(
                sys,
                "argv",
                [
                    "check_workflow_integrity.py",
                    "--workflows-dir",
                    str(workflows),
                    "--actions-dir",
                    str(root / "missing-actions"),
                ],
            ):
                self.assertEqual(0, integrity.main())


if __name__ == "__main__":
    unittest.main()
