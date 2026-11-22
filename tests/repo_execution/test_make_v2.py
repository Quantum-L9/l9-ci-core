"""make-v2: a declared bounded test phase beside the whole suite.

make-v1 has one test phase, and the organization's required check runs it on
every pull request, so the pull-request expense judge blocks any whole-tree
suite there. make-v2 adds ``test-fast`` (``repo-test-fast``): the required
check runs it on pull requests, merge groups and pushes, the judge holds it to
a bounded nonempty selection, and ``test`` stays the whole suite.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from v2_fixtures import ROOT, v2_contract  # noqa: E402

from l9_repo.contract import (  # noqa: E402
    FACADE_PHASES,
    PHASES_V2,
    ContractError,
    RepositoryExecution,
    render_facade,
    required_leaves,
    validate_repo_mk_text,
    validate_v2_contract_data,
)
from l9_repo.stamp import (  # noqa: E402
    render_contract,
    render_profile,
    stamp,
    verify_stamp,
)

sys.path.insert(0, str(ROOT / ".github" / "actions" / "run-repository-verification"))
sys.path.insert(0, str(ROOT / "tools"))

import pr_expense_guard as guard  # noqa: E402
import run as runner  # noqa: E402

REVISION = "a" * 40


def v2_repo_mk(fast: str, full: str = "pytest tests/") -> str:
    leaves = required_leaves("make-v2")
    lines = [".PHONY: " + " ".join(leaves), ""]
    for leaf in leaves:
        lines.append(f"{leaf}:")
        if leaf == "repo-test-fast":
            lines.append(f"\t{fast}")
        elif leaf == "repo-test":
            lines.append(f"\t{full}")
        else:
            lines.append("\t@true")
        lines.append("")
    return "\n".join(lines)


def write_v2_consumer(root: pathlib.Path, repo_mk: str) -> None:
    (root / ".l9").mkdir(parents=True, exist_ok=True)
    (root / ".l9" / "repo-workflow.json").write_text(
        json.dumps(v2_contract(facade="make-v2", required_phases=list(PHASES_V2))),
        encoding="utf-8",
    )
    (root / "Makefile").write_bytes(render_facade("make-v2"))
    (root / "Repo.mk").write_text(repo_mk, encoding="utf-8")
    test = root / "tests" / "unit" / "test_a.py"
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text("def test_ok():\n    assert True\n", encoding="utf-8")


class FacadeTests(unittest.TestCase):
    def test_make_v2_is_make_v1_plus_the_fast_phase(self) -> None:
        v1 = render_facade("make-v1").decode("utf-8")
        v2 = render_facade("make-v2").decode("utf-8")
        self.assertIn("test-fast: repo-test-fast ##", v2)
        self.assertNotIn("test-fast", v1)
        for phase in ("setup", "validate", "check", "test"):
            with self.subTest(phase=phase):
                self.assertIn(f"\n{phase}: repo-{phase} ##", v2)
        self.assertEqual(
            ("setup", "validate", "check", "test-fast", "test"),
            FACADE_PHASES["make-v2"],
        )

    def test_make_v1_is_unchanged(self) -> None:
        self.assertEqual(
            ("setup", "validate", "check", "test"), FACADE_PHASES["make-v1"]
        )


class ContractTests(unittest.TestCase):
    def test_make_v2_contract_requires_its_own_phase_list(self) -> None:
        validate_v2_contract_data(
            v2_contract(facade="make-v2", required_phases=list(PHASES_V2))
        )
        for phases in (
            ["setup", "validate", "check", "test"],
            ["setup", "validate", "check", "test", "test-fast"],
        ):
            with self.subTest(phases=phases):
                with self.assertRaisesRegex(ContractError, "make-v2"):
                    validate_v2_contract_data(
                        v2_contract(facade="make-v2", required_phases=phases)
                    )
        with self.assertRaises(ContractError):
            validate_v2_contract_data(v2_contract(required_phases=list(PHASES_V2)))

    def test_make_v2_repo_mk_must_implement_repo_test_fast(self) -> None:
        validate_repo_mk_text(v2_repo_mk("pytest tests/unit"), "make-v2")
        without = v2_repo_mk("pytest tests/unit").replace(
            "repo-test-fast:\n\tpytest tests/unit\n", ""
        )
        with self.assertRaisesRegex(ContractError, "repo-test-fast"):
            validate_repo_mk_text(without, "make-v2")

    def test_make_v2_repo_mk_may_not_redefine_test_fast(self) -> None:
        text = v2_repo_mk("pytest tests/unit") + "test-fast:\n\t@true\n"
        with self.assertRaisesRegex(ContractError, "facade target 'test-fast'"):
            validate_repo_mk_text(text, "make-v2")
        # make-v1 does not own test-fast, so a make-v1 Repo.mk may name it.
        validate_repo_mk_text(
            "\n".join(
                [
                    ".PHONY: repo-setup repo-validate repo-check repo-test",
                    *(
                        f"repo-{phase}:\n\t@true"
                        for phase in ("setup", "validate", "check", "test")
                    ),
                    "test-fast:\n\t@true",
                    "",
                ]
            )
        )

    def test_make_v2_consumer_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v2_consumer(root, v2_repo_mk("pytest tests/unit"))
            RepositoryExecution(root).validate()


class RunnerPhaseTests(unittest.TestCase):
    def test_judged_events_run_test_fast_under_make_v2(self) -> None:
        for event in ("pull_request", "pull_request_target", "merge_group", "push"):
            with self.subTest(event=event):
                self.assertEqual(
                    ("setup", "validate", "check", "test-fast"),
                    runner.v2_run_phases("make-v2", event),
                )

    def test_other_events_run_the_whole_suite_under_make_v2(self) -> None:
        for event in ("schedule", "workflow_dispatch", "release", ""):
            with self.subTest(event=event):
                self.assertEqual(
                    ("setup", "validate", "check", "test"),
                    runner.v2_run_phases("make-v2", event),
                )

    def test_make_v1_runs_its_four_phases_on_every_event(self) -> None:
        for event in ("pull_request", "schedule", ""):
            with self.subTest(event=event):
                self.assertEqual(
                    runner.V2_REQUIRED_PHASES, runner.v2_run_phases("make-v1", event)
                )

    def test_judged_events_match_the_judge(self) -> None:
        self.assertEqual(
            {"pull_request", "pull_request_target", "merge_group", "push"},
            set(runner.FAST_TEST_EVENTS),
        )
        self.assertLessEqual(set(runner.FAST_TEST_EVENTS), set(guard.JUDGED_EVENTS))


class JudgeTests(unittest.TestCase):
    def judge(self, root: pathlib.Path) -> guard.GuardDecision:
        return guard.judge(
            root,
            event="pull_request",
            scope_resolved=True,
            changed_files=("engine/a.py",),
        )

    def test_bounded_test_fast_passes_beside_a_whole_tree_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v2_consumer(
                root,
                v2_repo_mk("python3 -m pytest tests/unit -m unit", "pytest tests/"),
            )
            decision = self.judge(root)
            self.assertEqual("BOUNDED_FAST", decision.status, decision.findings)

    def test_whole_tree_test_fast_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v2_consumer(root, v2_repo_mk("python3 -m pytest tests/ -m unit"))
            decision = self.judge(root)
            self.assertEqual("REPO_TEST_FULL", decision.status, decision.findings)

    def test_make_v2_without_test_fast_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v2_consumer(root, v2_repo_mk("pytest tests/unit"))
            (root / "Makefile").write_bytes(render_facade("make-v1"))
            decision = self.judge(root)
            self.assertEqual("UNKNOWN_SCRIPT", decision.status, decision.findings)

    def test_unreadable_contract_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v2_consumer(root, v2_repo_mk("pytest tests/unit"))
            (root / ".l9" / "repo-workflow.json").write_text("{", encoding="utf-8")
            decision = self.judge(root)
            self.assertEqual("UNKNOWN_SCRIPT", decision.status, decision.findings)

    def test_make_v1_still_judges_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v2_consumer(root, v2_repo_mk("pytest tests/unit"))
            (root / ".l9" / "repo-workflow.json").write_text(
                json.dumps(v2_contract()), encoding="utf-8"
            )
            self.assertEqual("test", guard.required_test_phase(root))
            decision = self.judge(root)
            self.assertEqual("REPO_TEST_FULL", decision.status, decision.findings)


class StampTests(unittest.TestCase):
    def test_contract_and_makefile_follow_the_facade(self) -> None:
        contract = json.loads(render_contract("make-v2"))
        self.assertEqual("make-v2", contract["facade"])
        self.assertEqual(list(PHASES_V2), contract["required_phases"])
        rendered = render_profile("python", "make-v2")
        self.assertEqual(render_facade("make-v2"), rendered["Makefile"])
        self.assertEqual(render_profile("python")["ruff.toml"], rendered["ruff.toml"])

    def test_a_make_v2_stamp_verifies_clean_and_is_kept_on_restamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            stamp(root, "python", REVISION, facade="make-v2")
            self.assertEqual(render_facade("make-v2"), (root / "Makefile").read_bytes())
            _record, findings = verify_stamp(root)
            self.assertEqual([], findings)
            self.assertEqual([], stamp(root, "python", REVISION))
            self.assertEqual(render_facade("make-v2"), (root / "Makefile").read_bytes())


if __name__ == "__main__":
    unittest.main()
