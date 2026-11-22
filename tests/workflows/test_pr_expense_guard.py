"""Pull-request expense guard.

Fixtures are not hosted receipts. A static ``BOUNDED_FAST`` does not mean a
suite ran. The known-bad cases are the acceptance rows for direct full
pytest, repo-test indirection, pre-commit indirection, a Sonar re-run, an
unknown nested script, zero tests, an unresolved diff, and a skipped required
check.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import pr_expense_guard as guard  # noqa: E402

ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
MAKE_V1 = ROOT / "tools" / "l9_repo" / "facades" / "make-v1.mk"
GUARD_SOURCE = ROOT / "tools" / "pr_expense_guard.py"


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def bounded_test(root: Path) -> None:
    write(
        root,
        "tests/unit/test_a.py",
        "def test_ok():\n    assert True\n",
    )


def workflow(body: str) -> str:
    return "name: CI\non:\n  pull_request:\njobs:\n" + body


def judge(root: Path, **overrides: object) -> guard.GuardDecision:
    event = str(overrides.pop("event", "pull_request"))
    scope = bool(overrides.pop("scope_resolved", True))
    changed = overrides.pop("changed_files", ("README.md",))
    assert isinstance(changed, tuple)
    return guard.judge(
        root,
        event=event,
        scope_resolved=scope,
        changed_files=changed,
    )


class KnownBadCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assert_static(self, decision: guard.GuardDecision, status: str) -> None:
        self.assertEqual(status, decision.status)
        self.assertFalse(decision.heavy)
        self.assertFalse(decision.suite_pass)
        self.assertEqual("static", decision.receipt)

    def test_direct_full_pytest_blocks(self) -> None:
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  fast:\n    steps:\n      - run: pytest tests/\n"),
        )
        decision = judge(self.root)
        self.assert_static(decision, "FULL_PYTEST")
        self.assertEqual("none", decision.selected_suite)

    def test_workflow_only_diff_is_not_exempt(self) -> None:
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  fast:\n    steps:\n      - run: pytest tests/\n"),
        )
        decision = judge(
            self.root,
            changed_files=(".github/workflows/ci.yml",),
        )
        self.assert_static(decision, "FULL_PYTEST")
        self.assertEqual((".github/workflows/ci.yml",), decision.changed_files)

    def test_yaml_and_shell_comments_are_not_edges(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            "# pytest tests/\n"
            + workflow(
                "  fast:\n"
                "    steps:\n"
                "      - run: |\n"
                "          # pytest tests/\n"
                "          pytest tests/unit/test_a.py\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")
        self.assertTrue(decision.selected_suite.startswith("bounded:"))

    def test_repo_test_indirection_blocks_without_a_workflow_pytest_string(
        self,
    ) -> None:
        write(
            self.root,
            "Makefile",
            "include Repo.mk\ntest: repo-test\n",
        )
        write(
            self.root,
            "Repo.mk",
            "repo-test:\n\tpytest tests/\n",
        )
        text = workflow("  fast:\n    steps:\n      - run: make test\n")
        write(self.root, ".github/workflows/ci.yml", text)
        self.assertNotIn("pytest", text)
        decision = judge(self.root)
        self.assert_static(decision, "REPO_TEST_FULL")

    def test_pre_commit_indirection_blocks(self) -> None:
        write(
            self.root,
            ".pre-commit-config.yaml",
            "repos:\n"
            "  - repo: local\n"
            "    hooks:\n"
            "      - id: pytest\n"
            "        entry: pytest tests/\n"
            "        language: system\n"
            "        pass_filenames: false\n",
        )
        text = workflow(
            "  hooks:\n    steps:\n      - run: pre-commit run --all-files\n"
        )
        write(self.root, ".github/workflows/ci.yml", text)
        self.assertNotIn("pytest", text)
        decision = judge(self.root)
        self.assert_static(decision, "PRE_COMMIT_PYTEST")

    def test_sonar_rerun_blocks(self) -> None:
        write(
            self.root,
            ".github/workflows/sonar.yml",
            workflow(
                "  sonar:\n"
                "    name: Sonar analysis\n"
                "    steps:\n"
                "      - run: pytest tests/\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "SONAR_PYTEST")

    def test_unknown_nested_script_blocks(self) -> None:
        write(self.root, "scripts/outer.sh", "#!/bin/bash\nbash scripts/inner.sh\n")
        text = workflow("  fast:\n    steps:\n      - run: bash scripts/outer.sh\n")
        write(self.root, ".github/workflows/ci.yml", text)
        self.assertNotIn("pytest", text)
        decision = judge(self.root)
        self.assert_static(decision, "UNKNOWN_SCRIPT")

    def test_zero_tests_block(self) -> None:
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  docs:\n    steps:\n      - run: echo check the docs\n"),
        )
        decision = judge(self.root, changed_files=("docs/README.md",))
        self.assert_static(decision, "ZERO_TESTS")

    def test_unresolved_scope_blocks_without_falling_back_to_the_catalog(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  fast:\n    steps:\n      - run: pytest tests/unit/test_a.py\n"),
        )
        decision = judge(
            self.root,
            scope_resolved=False,
            changed_files=(".", "tests/"),
        )
        self.assert_static(decision, "PR_SCOPE_UNKNOWN")
        self.assertEqual("none", decision.selected_suite)

    def test_skipped_required_check_blocks(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n"
                "    name: Fast tests\n"
                "    if: false\n"
                "    steps:\n"
                "      - run: pytest tests/unit/test_a.py\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "SKIPPED_REQUIRED")

    def test_bounded_nonempty_fast_suite_passes_as_a_static_receipt(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  fast:\n    steps:\n      - run: pytest tests/unit/test_a.py\n"),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")
        self.assertNotEqual("none", decision.selected_suite)
        self.assertIn("suite_pass=false", guard.protocol(decision))
        self.assertIn("receipt=static", guard.protocol(decision))

    def test_schedule_only_full_catalog_does_not_block_the_fast_job(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            "name: CI\n"
            "on:\n"
            "  pull_request:\n"
            "  schedule:\n"
            "    - cron: '17 7 * * *'\n"
            "jobs:\n"
            "  fast:\n"
            "    if: github.event_name == 'pull_request'\n"
            "    steps:\n"
            "      - run: pytest tests/unit/test_a.py\n"
            "  deep:\n"
            "    if: github.event_name == 'schedule'\n"
            "    steps:\n"
            "      - run: pytest tests/\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")


class ScopeResolutionTests(unittest.TestCase):
    def test_a_single_parent_commit_does_not_become_the_whole_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "README.md", "docs\n")
            _git(root, ["init", "-b", "main"])
            _git(root, ["add", "README.md"])
            _commit(root, "docs")
            resolved, files = guard.resolve_scope(root, "pull_request", "")
            self.assertFalse(resolved)
            self.assertEqual((), files)
            self.assertNotIn(".", files)
            self.assertNotIn("tests", files)
            self.assertNotIn("tests/", files)

    def test_a_merge_commit_names_the_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git(root, ["init", "-b", "main"])
            write(root, "README.md", "base\n")
            _git(root, ["add", "README.md"])
            _commit(root, "base")
            _git(root, ["checkout", "-b", "pr"])
            write(root, "tests/unit/test_a.py", "def test_ok():\n    assert True\n")
            _git(root, ["add", "tests/unit/test_a.py"])
            _commit(root, "add a test")
            _git(root, ["checkout", "main"])
            _git(
                root,
                [
                    "-c",
                    "user.email=expense-guard@example.com",
                    "-c",
                    "user.name=expense-guard",
                    "merge",
                    "--no-ff",
                    "pr",
                    "-m",
                    "merge",
                ],
            )
            resolved, files = guard.resolve_scope(root, "pull_request", "")
            self.assertTrue(resolved)
            self.assertEqual(("tests/unit/test_a.py",), files)


class CoreTreeTests(unittest.TestCase):
    def test_this_revision_has_a_bounded_fast_selection(self) -> None:
        for event in ("pull_request", "push"):
            with self.subTest(event=event):
                decision = guard.judge(
                    ROOT,
                    event=event,
                    scope_resolved=True,
                    changed_files=("README.md",),
                )
                self.assertEqual("BOUNDED_FAST", decision.status)
                self.assertFalse(decision.suite_pass)
                self.assertEqual("static", decision.receipt)
                self.assertTrue(decision.selected_suite.startswith("bounded:"))

    def test_make_v1_facade_is_byte_identical_to_the_released_template(self) -> None:
        self.assertEqual(
            MAKE_V1.read_bytes(),
            (ROOT / "Makefile").read_bytes(),
        )


class RequiredWorkflowTests(unittest.TestCase):
    def test_the_judge_runs_the_workflow_checkout_not_a_pasted_copy(self) -> None:
        document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        steps = document["jobs"]["analyze"]["steps"]
        names = [step.get("name") for step in steps]
        checkout = names.index("Checkout immutable event revision")
        judge_at = names.index("Judge pull-request test expense")
        verify = names.index("Run repository verification contract")
        self.assertLess(checkout, judge_at)
        self.assertLess(judge_at, verify)
        step = steps[judge_at]
        condition = str(step["if"])
        self.assertIn("github.event_name == 'pull_request'", condition)
        self.assertIn("github.event_name == 'merge_group'", condition)
        self.assertIn("github.event_name == 'push'", condition)
        self.assertEqual(
            "${{ steps.runtime.outputs.event-class }}",
            step["env"]["GH_EVENT"],
        )
        self.assertEqual(
            "${{ github.event.before || '' }}", step["env"]["GUARD_BEFORE"]
        )
        self.assertEqual(
            "${{ github.event_name == 'workflow_call' && github.workflow_sha || github.sha }}",
            step["env"]["CORE_SHA"],
        )
        self.assertEqual("${{ github.workspace }}", step["env"]["GUARD_WORKSPACE"])
        run = str(step["run"])
        workflow = ORG_CI.read_text(encoding="utf-8")
        source = GUARD_SOURCE.read_text(encoding="utf-8")
        self.assertIn("https://github.com/Quantum-L9/l9-ci-core.git", run)
        self.assertIn('fetch --depth=1 origin "${CORE_SHA}"', run)
        self.assertIn('"${core}/tools/pr_expense_guard.py"', run)
        self.assertIn('runpy.run_path(os.environ["CORE_GUARD"]', run)
        self.assertNotIn("python3 tools/pr_expense_guard.py", run)
        self.assertNotIn("python3 ./tools/pr_expense_guard.py", run)
        self.assertNotIn(source, workflow)
        self.assertNotIn("def resolve_scope", workflow)
        self.assertNotIn("L9_GUARD_PY", workflow)
        self.assertNotIn("make ", run)
        self.assertNotIn("pytest", run)
        self.assertNotIn("secrets.", str(step))
        self.assertNotIn("github.token", str(step))
        self.assertNotIn("GITHUB_TOKEN", run)
        self.assertNotIn("x-access-token", run)

    def test_push_scope_fetch_happens_before_the_token_is_cleared(self) -> None:
        document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        checkout = next(
            step
            for step in document["jobs"]["analyze"]["steps"]
            if step.get("name") == "Checkout immutable event revision"
        )
        run = str(checkout["run"])
        self.assertLess(
            run.index('fetch --depth=1 origin "${BEFORE}"'),
            run.index("git remote set-url origin"),
        )
        self.assertLess(
            run.index("git checkout --detach FETCH_HEAD"),
            run.index('fetch --depth=1 origin "${BEFORE}"'),
        )
        self.assertEqual("${{ github.event.before || '' }}", checkout["env"]["BEFORE"])

    def test_guard_source_reads_no_token(self) -> None:
        source = GUARD_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("GITHUB_TOKEN", source)
        self.assertNotIn("secrets.", source)
        self.assertNotIn("Authorization", source)


def _git(root: Path, args: list[str]) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.strip() or completed.stdout.strip())


def _commit(root: Path, message: str) -> None:
    _git(
        root,
        [
            "-c",
            "user.email=expense-guard@example.com",
            "-c",
            "user.name=expense-guard",
            "commit",
            "-m",
            message,
        ],
    )


if __name__ == "__main__":
    unittest.main()
