"""Repo.mk is repository-owned; Core proves only its structural ABI."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import PHASES, logging_repo_mk, write_consumer  # noqa: E402

from l9_repo.contract import (  # noqa: E402
    FACADE_TARGETS,
    REQUIRED_LEAVES,
    ContractError,
    RepositoryExecution,
    implemented_leaves,
    make_phony_targets,
    make_target_declarations,
    validate_repo_mk_text,
)

PHONY_LINE = ".PHONY: " + " ".join(REQUIRED_LEAVES) + "\n"


def leaves_with(implementation: dict[str, str]) -> str:
    """A Repo.mk whose leaves are implemented as given; others get a recipe."""

    parts = [PHONY_LINE]
    for leaf in REQUIRED_LEAVES:
        parts.append(implementation.get(leaf, f"{leaf}:\n\t@:\n"))
        parts.append("\n")
    return "".join(parts)


class RepoMkAbiTests(unittest.TestCase):
    def test_required_leaves_are_the_four_phases(self) -> None:
        self.assertEqual(
            ("repo-setup", "repo-validate", "repo-check", "repo-test"), REQUIRED_LEAVES
        )

    def test_minimal_conforming_repo_mk_passes(self) -> None:
        validate_repo_mk_text(logging_repo_mk())

    def test_each_missing_leaf_is_named(self) -> None:
        for phase in PHASES:
            with self.subTest(missing=phase):
                text = "".join(
                    f".PHONY: repo-{other}\nrepo-{other}:\n\t@:\n\n"
                    for other in PHASES
                    if other != phase
                )
                with self.assertRaisesRegex(ContractError, f"missing .*repo-{phase}"):
                    validate_repo_mk_text(text)

    def test_required_leaf_must_be_phony(self) -> None:
        text = "".join(f"repo-{phase}:\n\t@:\n\n" for phase in PHASES)
        with self.assertRaisesRegex(
            ContractError, r"repo-setup must be declared \.PHONY"
        ):
            validate_repo_mk_text(text)
        partial = ".PHONY: repo-setup repo-validate repo-check\n" + text
        with self.assertRaisesRegex(
            ContractError, r"repo-test must be declared \.PHONY"
        ):
            validate_repo_mk_text(partial)

    def test_phony_declarations_may_be_split_and_continued(self) -> None:
        text = (
            ".PHONY: repo-setup \\\n"
            "\trepo-validate\n"
            "\n"
            ".PHONY: repo-check repo-test lint  # local\n"
            + "".join(f"repo-{phase}:\n\t@:\n\n" for phase in PHASES)
            + "lint: repo-check\n"
        )
        self.assertEqual({*REQUIRED_LEAVES, "lint"}, make_phony_targets(text))
        validate_repo_mk_text(text)

    def test_repository_local_targets_may_coexist(self) -> None:
        extra = "\n# local convenience\n.PHONY: lint doctor bench\nlint: repo-check\ndoctor:\n\t@:\nbench:\n\t@:\n"
        validate_repo_mk_text(logging_repo_mk(extra=extra))

    def test_repo_mk_may_not_redefine_a_facade_verb(self) -> None:
        for target in sorted(FACADE_TARGETS):
            with self.subTest(target=target):
                text = logging_repo_mk(extra=f"\n{target}:\n\t@true\n")
                with self.assertRaisesRegex(
                    ContractError, rf"Repo\.mk:\d+ redefines facade target '{target}'"
                ):
                    validate_repo_mk_text(text)

    def test_target_parser_ignores_assignments_recipes_and_special_targets(
        self,
    ) -> None:
        text = (
            "PYTHON ?= python3\n"
            "TOOL := $(PYTHON) -m tool\n"
            "LATE ::= value\n"
            ".PHONY: \\\n"
            "\trepo-setup\n"
            "repo-setup repo-test: ## two targets\n"
            "\t@echo pr: not a rule\n"
            "lint: repo-check\n"
        )
        self.assertEqual(
            [(6, "repo-setup"), (6, "repo-test"), (8, "lint")],
            make_target_declarations(text),
        )
        self.assertEqual({"repo-setup"}, make_phony_targets(text))


class RepoMkImplementationTests(unittest.TestCase):
    """A declared leaf is not an implemented leaf.

    GNU Make runs a ``.PHONY`` rule with neither prerequisites nor recipe and
    exits zero, so such a leaf would let its phase pass with zero work. The
    structural validator rejects that shape and the shape where the only
    recipe sits inside a conditional. It does not judge what a recipe does:
    ``@:`` is admitted here and is the concern of actual-work evidence.
    """

    def test_bare_phony_leaf_with_no_recipe_or_prerequisite_is_rejected(self) -> None:
        for leaf in REQUIRED_LEAVES:
            with self.subTest(leaf=leaf):
                text = leaves_with({leaf: f"{leaf}:\n"})
                with self.assertRaisesRegex(
                    ContractError,
                    rf"{leaf} declares no unconditional recipe or prerequisite",
                ):
                    validate_repo_mk_text(text)

    def test_all_four_leaves_bare_names_the_first_leaf(self) -> None:
        text = PHONY_LINE + "".join(f"{leaf}:\n" for leaf in REQUIRED_LEAVES)
        with self.assertRaisesRegex(ContractError, "repo-setup declares no"):
            validate_repo_mk_text(text)

    def test_recipe_only_inside_an_inactive_conditional_is_rejected(self) -> None:
        text = leaves_with({"repo-test": "repo-test:\nifdef NEVER\n\t@pytest\nendif\n"})
        with self.assertRaisesRegex(
            ContractError, "repo-test declares no unconditional recipe"
        ):
            validate_repo_mk_text(text)

    def test_recipe_split_across_both_conditional_branches_is_rejected(self) -> None:
        text = leaves_with(
            {
                "repo-test": (
                    "repo-test:\nifeq ($(CI),1)\n\t@pytest -x\nelse\n\t@pytest\nendif\n"
                )
            }
        )
        with self.assertRaisesRegex(ContractError, "repo-test declares no"):
            validate_repo_mk_text(text)

    def test_target_specific_variable_is_not_an_implementation(self) -> None:
        text = leaves_with({"repo-test": "repo-test: PYTEST_ARGS = -x\n"})
        with self.assertRaisesRegex(ContractError, "repo-test declares no"):
            validate_repo_mk_text(text)

    def test_bare_order_only_separator_is_not_a_prerequisite(self) -> None:
        # `repo-test: |` names nothing on either side of the separator; GNU
        # make reports "Nothing to be done" and exits zero.
        text = leaves_with({"repo-test": "repo-test: |\n"})
        with self.assertRaisesRegex(ContractError, "repo-test declares no"):
            validate_repo_mk_text(text)
        text = leaves_with({"repo-test": "repo-test: |  # order-only, empty\n"})
        with self.assertRaisesRegex(ContractError, "repo-test declares no"):
            validate_repo_mk_text(text)

    def test_order_only_prerequisite_is_an_implementation(self) -> None:
        text = leaves_with(
            {"repo-test": ".PHONY: unit\nrepo-test: | unit\nunit:\n\t@pytest\n"}
        )
        validate_repo_mk_text(text)

    def test_recipe_under_a_custom_recipe_prefix_is_an_implementation(self) -> None:
        text = ".RECIPEPREFIX = >\n" + leaves_with(
            {"repo-test": "repo-test:\n>@pytest\n"}
        ).replace("\t@:", ">@:")
        validate_repo_mk_text(text)
        self.assertEqual(set(REQUIRED_LEAVES), implemented_leaves(text))

    def test_recipe_prefix_reset_to_tab_is_honoured(self) -> None:
        text = (
            ".RECIPEPREFIX = >\n"
            ".RECIPEPREFIX =\n"  # empty value restores the tab
            + leaves_with({"repo-test": "repo-test:\n>@pytest\n"})
        )
        with self.assertRaisesRegex(ContractError, "repo-test declares no"):
            validate_repo_mk_text(text)

    def test_dependency_only_aggregate_is_a_valid_implementation(self) -> None:
        text = leaves_with(
            {
                "repo-test": (
                    ".PHONY: unit integration\n"
                    "repo-test: unit integration\n"
                    "unit:\n\t@pytest tests/unit\n"
                    "integration:\n\t@pytest tests/integration\n"
                )
            }
        )
        validate_repo_mk_text(text)
        self.assertEqual(set(REQUIRED_LEAVES), implemented_leaves(text))

    def test_inline_semicolon_recipe_is_an_implementation(self) -> None:
        validate_repo_mk_text(leaves_with({"repo-test": "repo-test: ; @pytest\n"}))

    def test_continued_rule_line_and_comment_lines_are_tolerated(self) -> None:
        text = leaves_with(
            {
                "repo-test": (
                    "repo-test: \\\n"
                    "\tunit\n"
                    "# a comment between rule and recipe\n"
                    "\n"
                    "\t@pytest\n"
                    ".PHONY: unit\nunit:\n\t@true\n"
                )
            }
        )
        validate_repo_mk_text(text)

    def test_leaf_declared_twice_counts_either_rule(self) -> None:
        text = leaves_with(
            {
                "repo-test": "repo-test:\n\n.PHONY: unit\nrepo-test: unit\nunit:\n\t@true\n"
            }
        )
        validate_repo_mk_text(text)

    def test_trivial_success_recipe_is_structurally_admitted(self) -> None:
        # Known limitation, by design: structure cannot see that ``@:`` tests
        # nothing. Actual-work evidence is a separate enforcement concern.
        validate_repo_mk_text(leaves_with({"repo-test": "repo-test:\n\t@:\n"}))

    def test_no_work_leaf_fails_full_admission_though_make_exits_zero(self) -> None:
        from v2_fixtures import make

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(
                root, repo_mk=leaves_with({"repo-test": "repo-test:\n"}), init_git=False
            )
            self.assertEqual(0, make(root, "test").returncode)
            with self.assertRaisesRegex(ContractError, "repo-test declares no"):
                RepositoryExecution(root).validate()


UNIT_RAN = "unit-target-ran"
UNIT_RULE = f".PHONY: unit\nunit:\n\t@echo {UNIT_RAN}\n"

# Variable-only prerequisites that GNU Make expands to nothing when it reads
# the rule line, so the phase "passes" with "Nothing to be done".
EXPANDS_EMPTY = {
    "undefined": "repo-test: $(UNDEFINED_PREREQUISITE)\n",
    "assigned_empty": "TESTS :=\nrepo-test: $(TESTS)\n",
    "defined_after_rule": "repo-test: $(TESTS)\nTESTS := unit\n",
    "emptied_before_rule": "TESTS := unit\nTESTS :=\nrepo-test: $(TESTS)\n",
    "undefined_before_rule": "TESTS := unit\nundefine TESTS\nrepo-test: $(TESTS)\n",
    "recursive_to_undefined": "TESTS = $(LATER)\nrepo-test: ${TESTS}\nLATER := unit\n",
}


class RepoMkVariablePrerequisiteTests(unittest.TestCase):
    """A variable-only prerequisite is an implementation only when Repo.mk
    proves it non-empty above the rule (GNU Make expands it on that line)."""

    def test_variable_that_expands_empty_is_rejected(self) -> None:
        for case, rule in EXPANDS_EMPTY.items():
            with self.subTest(case=case):
                text = leaves_with({"repo-test": rule + UNIT_RULE})
                with self.assertRaisesRegex(ContractError, "repo-test declares no"):
                    validate_repo_mk_text(text)

    def test_unprovable_variable_fails_closed(self) -> None:
        cases = {
            # The environment may supply TESTS, possibly empty.
            "conditional_default": "TESTS ?= unit\nrepo-test: $(TESTS)\n",
            "shell_assignment": "TESTS != echo unit\nrepo-test: $(TESTS)\n",
            "only_inside_conditional": (
                "ifdef CI\nTESTS := unit\nendif\nrepo-test: $(TESTS)\n"
            ),
            "only_inside_define": "define TESTS\nunit\nendef\nrepo-test: $(TESTS)\n",
            "function_call": "repo-test: $(wildcard tests/unit)\n",
            "substitution_reference": "SRC := unit.c\nrepo-test: $(SRC:.c=)\n",
            "automatic_variable": "repo-test: $@\n",
            "reference_cycle": "A = $(B)\nB = $(A)\nrepo-test: $(A)\n",
        }
        for case, rule in cases.items():
            with self.subTest(case=case):
                text = leaves_with({"repo-test": rule + UNIT_RULE})
                with self.assertRaisesRegex(ContractError, "repo-test declares no"):
                    validate_repo_mk_text(text)

    def test_variable_proven_non_empty_above_the_rule_is_an_implementation(
        self,
    ) -> None:
        cases = {
            "simple": "TESTS := unit\nrepo-test: $(TESTS)\n",
            "recursive": "TESTS = unit\nrepo-test: $(TESTS)\n",
            "braces": "TESTS := unit\nrepo-test: ${TESTS}\n",
            "chained": "INNER := unit\nTESTS = $(INNER)\nrepo-test: $(TESTS)\n",
            "append": "TESTS :=\nTESTS += unit\nrepo-test: $(TESTS)\n",
            "append_after_default": "TESTS ?=\nTESTS += unit\nrepo-test: $(TESTS)\n",
            "override_export": "override export TESTS := unit\nrepo-test: $(TESTS)\n",
            "comment_after_value": "TESTS := unit # the suite\nrepo-test: $(TESTS)\n",
            "literal_beside_variable": "repo-test: unit $(UNDEFINED)\n",
            "literal_around_variable": "repo-test: un$(EMPTY)it\n",
        }
        for case, rule in cases.items():
            with self.subTest(case=case):
                text = leaves_with({"repo-test": rule + UNIT_RULE})
                validate_repo_mk_text(text)
                self.assertIn("repo-test", implemented_leaves(text))

    def test_expands_empty_leaf_fails_full_admission_though_make_exits_zero(
        self,
    ) -> None:
        from v2_fixtures import make

        for case in ("undefined", "defined_after_rule", "emptied_before_rule"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                write_consumer(
                    root,
                    repo_mk=leaves_with({"repo-test": EXPANDS_EMPTY[case] + UNIT_RULE}),
                    init_git=False,
                )
                completed = make(root, "test")
                self.assertEqual(0, completed.returncode)
                self.assertNotIn(UNIT_RAN, completed.stdout)
                with self.assertRaisesRegex(ContractError, "repo-test declares no"):
                    RepositoryExecution(root).validate()


class RepoMkOwnershipTests(unittest.TestCase):
    def test_missing_repo_mk_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            (root / "Repo.mk").unlink()
            with self.assertRaisesRegex(
                ContractError, "missing repository implementation"
            ):
                RepositoryExecution(root).validate()

    def test_repo_mk_edits_survive_validation_and_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            extra = "\n# owned by this repository\n.PHONY: bench\nbench:\n\t@true\n"
            write_consumer(root, repo_mk=logging_repo_mk(extra=extra))
            edited = (root / "Repo.mk").read_bytes()
            execution = RepositoryExecution(root)
            execution.validate()
            execution.reconcile()
            execution.reconcile()
            self.assertEqual(edited, (root / "Repo.mk").read_bytes())
            self.assertNotIn(b"GENERATED", edited)

    def test_repo_mk_carries_no_generated_provenance_and_needs_no_extension_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            RepositoryExecution(root).validate()
            self.assertFalse((root / "Repo.local.mk").exists())
            text = (root / "Repo.mk").read_text(encoding="utf-8")
            self.assertNotIn("make-plan", text)
            self.assertNotIn("Repo.local.mk", text)


if __name__ == "__main__":
    unittest.main()
