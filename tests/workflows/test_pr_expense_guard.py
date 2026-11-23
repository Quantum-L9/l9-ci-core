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

    def test_make_function_beside_bounded_pytest_is_not_unknown(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            "Makefile",
            "REPO_PY := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)\n"
            "test:\n"
            "\tpytest tests/unit/test_a.py\n",
        )
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  fast:\n    steps:\n      - run: pytest tests/unit/test_a.py\n"),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")
        self.assertFalse(
            any(item.startswith("UNKNOWN_SCRIPT") for item in decision.findings)
        )

    def test_missing_docs_script_is_not_a_test_command(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n"
                "    steps:\n"
                "      - run: pytest tests/unit/test_a.py\n"
                "  docs:\n"
                "    steps:\n"
                "      - run: python scripts/docs_sync/validate_api_docs.py\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")
        self.assertFalse(
            any("validate_api_docs.py" in item for item in decision.findings)
        )

    def test_missing_test_recipe_script_stays_unknown(self) -> None:
        write(
            self.root,
            "Makefile",
            "test:\n\tpython scripts/run_the_tests.py\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "UNKNOWN_SCRIPT")

    # Cursor-Governance (Analyze (central Core) on every PR, 2026-11-23):
    # five constructs the judge misread as unknown scripts.

    def test_include_expands_a_variable_naming_several_fragments(self) -> None:
        bounded_test(self.root)
        write(self.root, "Makefile", "include Repo.mk\ntest: repo-test\n")
        write(
            self.root,
            "Repo.mk",
            "FRAGMENTS := \\\n\tops/make/a.mk \\\n\tops/make/b.mk\n"
            "include $(FRAGMENTS)\n",
        )
        write(self.root, "ops/make/a.mk", "repo-test: inner\n")
        write(self.root, "ops/make/b.mk", "inner:\n\tpytest tests/unit\n")
        decision = judge(self.root, changed_files=("engine/a.py",))
        self.assert_static(decision, "BOUNDED_FAST")

    def test_an_unresolved_include_word_stays_unknown(self) -> None:
        write(self.root, "Makefile", "include $(NOT_SET)\ntest:\n\tpytest tests\n")
        decision = judge(self.root, changed_files=("engine/a.py",))
        self.assert_static(decision, "UNKNOWN_SCRIPT")

    def test_a_top_level_make_diagnostic_is_not_an_unreadable_line(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            "Makefile",
            "ifneq ($(PLAN),)\n$(error make campaign accepts one plan path)\nendif\n"
            "$(warning legacy target)\n"
            "test:\n\tpytest tests/unit\n",
        )
        decision = judge(self.root, changed_files=("engine/a.py",))
        self.assert_static(decision, "BOUNDED_FAST")

    def test_curdir_is_the_repository_root(self) -> None:
        bounded_test(self.root)
        write(self.root, "scripts/run.sh", "#!/bin/bash\npytest tests/unit\n")
        write(self.root, "Makefile", 'test:\n\tbash "$(CURDIR)/scripts/run.sh"\n')
        decision = judge(self.root, changed_files=("engine/a.py",))
        self.assert_static(decision, "BOUNDED_FAST")

    def test_argparse_help_text_is_not_a_command(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            "scripts/runner.py",
            "import argparse, subprocess\n"
            "parser = argparse.ArgumentParser(description='pytest wrapper')\n"
            "parser.add_argument('rest', nargs='*', help='pytest args after --')\n"
            "subprocess.run(['pytest', 'tests/unit'], check=True)\n",
        )
        text = workflow("  fast:\n    steps:\n      - run: python scripts/runner.py\n")
        write(self.root, ".github/workflows/ci.yml", text)
        decision = judge(self.root)
        self.assertNotIn(
            "UNKNOWN_SCRIPT unclassified pytest option --", decision.findings
        )

    def test_a_variable_assigned_one_literal_path_is_a_literal_selection(self) -> None:
        bounded_test(self.root)
        text = workflow(
            "  fast:\n    steps:\n      - run: |\n"
            "          t=tests/unit/test_a.py\n"
            '          [ -f "$t" ] || exit 1\n'
            '          python3 -m pytest -q "$t"\n'
        )
        write(self.root, ".github/workflows/ci.yml", text)
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_a_variable_assigned_twice_stays_unresolved(self) -> None:
        bounded_test(self.root)
        text = workflow(
            "  fast:\n    steps:\n      - run: |\n"
            "          t=tests/unit/test_a.py\n"
            "          t=tests\n"
            '          pytest "$t"\n'
        )
        write(self.root, ".github/workflows/ci.yml", text)
        decision = judge(self.root)
        self.assert_static(decision, "UNKNOWN_SCRIPT")

    def test_a_variable_from_a_substitution_stays_unresolved(self) -> None:
        bounded_test(self.root)
        text = workflow(
            "  fast:\n    steps:\n      - run: |\n"
            "          t=$(cat selection.txt)\n"
            '          pytest "$t"\n'
        )
        write(self.root, ".github/workflows/ci.yml", text)
        decision = judge(self.root)
        self.assert_static(decision, "UNKNOWN_SCRIPT")

    def _make_v1_repo_test(self, recipe: str) -> None:
        write(self.root, "Makefile", "include Repo.mk\ntest: repo-test\n")
        write(
            self.root,
            "Repo.mk",
            "VENV ?= .venv\n"
            "REPO_PY := $(if $(wildcard $(VENV)/bin/python),"
            "$(VENV)/bin/python,python3)\n"
            "repo-test:\n\t" + recipe + "\n",
        )

    def test_interpreter_from_a_make_function_is_still_judged(self) -> None:
        # Cognitive.Engine.Graphs: the make function used to expand to
        # nothing, the command word vanished, and a whole-tree suite was
        # reported as ZERO_TESTS.
        bounded_test(self.root)
        self._make_v1_repo_test(
            '@PYTHONPATH="$${PYTHONPATH:-}:." $(REPO_PY) -m pytest tests/ -m unit -q'
        )
        decision = judge(self.root, changed_files=("engine/a.py",))
        self.assert_static(decision, "REPO_TEST_FULL")
        self.assertIn("REPO_TEST_FULL pytest tests/", decision.findings)

    def test_bounded_suite_behind_a_make_function_interpreter_passes(self) -> None:
        bounded_test(self.root)
        self._make_v1_repo_test(
            '@PYTHONPATH="$${PYTHONPATH:-}:." $(REPO_PY) -m pytest tests/unit -m unit'
        )
        decision = judge(self.root, changed_files=("engine/a.py",))
        self.assert_static(decision, "BOUNDED_FAST")

    def test_prefix_assignment_does_not_hide_a_full_suite(self) -> None:
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  fast:\n    steps:\n      - run: CI=1 env -u X pytest tests/\n"),
        )
        decision = judge(self.root)
        self.assert_static(decision, "FULL_PYTEST")

    def test_a_shell_function_interpreter_is_judged_by_its_arguments(self) -> None:
        # EIE: PYTHON := $(shell ...) picks the interpreter; `-m pytest tests/`
        # is still a whole-tree suite, not an undecided command.
        write(
            self.root,
            "Makefile",
            "PY := $(shell command -v python3)\ntest:\n\t$(PY) -m pytest tests/\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "REPO_TEST_FULL")

    def test_a_shell_function_interpreter_with_a_bounded_suite_passes(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            "Makefile",
            "PY := $(shell command -v python3)\n"
            "test:\n\t$(PY) -I -m pytest tests/unit/test_a.py\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_an_undecidable_command_word_in_a_test_recipe_is_unknown(self) -> None:
        write(
            self.root,
            "Makefile",
            "RUNNER := $(shell command -v tox)\ntest:\n\t$(RUNNER) -e py312\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "UNKNOWN_SCRIPT")

    def test_a_setup_script_behind_a_variable_is_not_a_test_command(self) -> None:
        # EIE: `bash "$GATE_RAIL/scripts/gen_env.sh"` in an e2e step and
        # `bash "$subject/tools/deps/install_locked.sh"` install or generate;
        # neither is the suite.
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n"
                "    steps:\n"
                '      - run: bash "$GATE_RAIL/scripts/gen_env.sh" base.env\n'
                '      - run: PYTHON=py bash "$subject/tools/deps/install_locked.sh"\n'
                "      - run: $TOOLS/bin/setup.sh\n"
                "      - run: pytest tests/unit/test_a.py\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_a_test_script_behind_a_variable_stays_unknown(self) -> None:
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                '  fast:\n    steps:\n      - run: bash "$RAIL/scripts/run_tests.sh"\n'
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "UNKNOWN_SCRIPT")

    def test_gnu_make_directives_are_read_not_refused(self) -> None:
        # l9-graphiti-memory: `export L9_SUITE_PROFILE` made the whole
        # Makefile unreadable and every PR UNKNOWN_SCRIPT.
        bounded_test(self.root)
        write(
            self.root,
            "Makefile",
            "L9_SUITE_PROFILE ?= local\n"
            "export L9_SUITE_PROFILE\n"
            "export A := 1\n"
            "unexport B\n"
            "export\n"
            "override C = 1\n"
            "private D = 2\n"
            "vpath %.c src\n"
            "undefine C\n"
            "define HELP\n"
            "usage: make test\n"
            "\tnot a recipe\n"
            "endef\n"
            "ifdef CI\n"
            "PY := python3\n"
            "else\n"
            "PY := python\n"
            "endif\n"
            "test:\n"
            "\t$(PY) -m pytest tests/unit/test_a.py\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_a_whole_tree_suite_inside_a_conditional_is_still_judged(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            "Makefile",
            "test:\n"
            "ifdef CI\n"
            "\tpytest tests/\n"
            "else\n"
            "\tpytest tests/unit/test_a.py\n"
            "endif\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "REPO_TEST_FULL")

    def test_a_workflow_level_working_directory_resolves_the_selection(self) -> None:
        # Constellation.Gate: `defaults: run: working-directory:` at the top of
        # the workflow made `pytest tests/integration` resolve from the
        # repository root and read as ZERO_TESTS.
        write(
            self.root,
            "pkg/tests/integration/test_a.py",
            "def test_ok():\n    assert True\n",
        )
        write(
            self.root,
            ".github/workflows/ci.yml",
            "name: CI\non:\n  pull_request:\n"
            "defaults:\n  run:\n    working-directory: pkg\n"
            "jobs:\n  fast:\n    steps:\n      - run: pytest -q tests/integration\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_a_job_working_directory_overrides_the_workflow_default(self) -> None:
        write(
            self.root,
            "pkg/tests/integration/test_a.py",
            "def test_ok():\n    assert True\n",
        )
        write(
            self.root,
            ".github/workflows/ci.yml",
            "name: CI\non:\n  pull_request:\n"
            "defaults:\n  run:\n    working-directory: pkg\n"
            "jobs:\n  fast:\n    defaults:\n      run:\n        working-directory: other\n"
            "    steps:\n      - run: pytest -q tests/integration\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "ZERO_TESTS")

    def test_pytest_cov_switches_are_flags(self) -> None:
        # l9-ci-sdk and l9-graphiti-memory: `--cov-branch` was "unclassified".
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n    steps:\n"
                "      - run: python -m pytest --cov=pkg --cov-branch --cov-append"
                " --cov-fail-under 90 --no-cov-on-fail tests/unit/test_a.py\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_a_subshell_does_not_glue_its_parenthesis_to_an_argument(self) -> None:
        # `(cd x && pytest -q)` produced "unclassified pytest option -q)".
        bounded_test(self.root)
        write(
            self.root,
            "Makefile",
            "test:\n\t(cd . && python -m pytest -q tests/unit/test_a.py)\n",
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_a_whole_tree_suite_inside_a_subshell_is_still_judged(self) -> None:
        bounded_test(self.root)
        write(self.root, "Makefile", "test:\n\t(cd . && python -m pytest -q)\n")
        decision = judge(self.root)
        self.assert_static(decision, "REPO_TEST_FULL")

    def test_a_checkout_path_working_directory_is_the_revision_root(self) -> None:
        # l9-graphiti-memory: actions/checkout `path: memory`, then
        # `working-directory: memory` with tests/unit/... paths read as
        # ZERO_TESTS because the judge looked under <root>/memory.
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "        with:\n          path: memory\n"
                "      - run: pytest -q tests/unit/test_a.py\n"
                "        working-directory: memory\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_another_repositorys_checkout_path_maps_nothing(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "        with:\n          repository: other/repo\n          path: peer\n"
                "      - run: pytest -q tests/unit/test_a.py\n"
                "        working-directory: peer\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "ZERO_TESTS")

    def test_pytest_collect_only_is_not_a_suite_run(self) -> None:
        # l9-graphiti-memory scripts/validate_release.sh:
        # `n=$(cd x && pytest --collect-only -q)` counts tests, runs none.
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n    steps:\n"
                "      - run: |\n"
                "          n=$(cd . && python -m pytest --collect-only -q)\n"
                "          pytest tests/unit/test_a.py\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_attached_pytest_report_characters_are_a_flag(self) -> None:
        bounded_test(self.root)
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow(
                "  fast:\n    steps:\n      - run: pytest -rfEs tests/unit/test_a.py\n"
            ),
        )
        decision = judge(self.root)
        self.assert_static(decision, "BOUNDED_FAST")

    def test_unclassified_make_test_recipe_is_unknown_not_zero(self) -> None:
        write(self.root, "Makefile", "test:\n\ttox -e py312\n")
        decision = judge(self.root)
        self.assert_static(decision, "UNKNOWN_SCRIPT")
        self.assertTrue(
            any("tox -e py312" in item for item in decision.findings),
            decision.findings,
        )

    def test_pytest_with_no_paths_still_fails(self) -> None:
        write(
            self.root,
            ".github/workflows/ci.yml",
            workflow("  fast:\n    steps:\n      - run: pytest\n"),
        )
        decision = judge(self.root)
        self.assert_static(decision, "FULL_PYTEST")


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
        self.assertEqual("${{ github.workflow_sha }}", step["env"]["CORE_SHA"])
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
