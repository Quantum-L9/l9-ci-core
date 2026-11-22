"""The make-v1 facade: tiny, deterministic, and free of publication.

The primary proof of the publication boundary is the target surface itself:
the facade declares exactly ``help`` plus the four phases. The substring
checks are regression tripwires on top of that.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import (  # noqa: E402
    PHASES,
    git,
    logging_repo_mk,
    make,
    phases_log,
    write_consumer,
)

from v2_fixtures import ROOT, v2_contract  # noqa: E402

from l9_repo.contract import (  # noqa: E402
    FACADE_PHASES,
    FACADE_TARGETS,
    PHASES_V2,
    ContractError,
    RepositoryExecution,
    make_phony_targets,
    make_target_declarations,
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

FORBIDDEN_TOKENS = (
    "L9",
    "l9 pr",
    "git push",
    "gh pr",
    "pr:",
    "push:",
    "publish:",
    "release:",
    "deploy:",
    "start:",
    "workspace-clean:",
    "wiring-check:",
)


class FacadeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = render_facade("make-v1").decode("utf-8")

    def test_facade_declares_exactly_help_and_the_four_phases(self) -> None:
        declared = [target for _, target in make_target_declarations(self.text)]
        self.assertEqual(["help", *PHASES], declared)
        self.assertEqual(set(declared), FACADE_TARGETS)
        self.assertEqual(FACADE_TARGETS, make_phony_targets(self.text))

    def test_each_phase_is_exactly_one_repository_leaf(self) -> None:
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertRegex(self.text, rf"(?m)^{phase}: repo-{phase}( ##.*)?$")

    def test_facade_includes_repo_mk_by_composition_only(self) -> None:
        self.assertRegex(self.text, r"(?m)^include Repo\.mk$")
        self.assertNotIn("-include", self.text)
        self.assertNotIn("$(MAKE)", self.text)
        self.assertNotIn("-f Repo.mk", self.text)

    def test_facade_names_its_own_version(self) -> None:
        self.assertIn("make-v1", self.text.splitlines()[0])

    def test_facade_contains_no_publication_or_governance_vocabulary(self) -> None:
        for token in FORBIDDEN_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)

    def test_facade_recipes_run_nothing_but_help(self) -> None:
        recipes = [line for line in self.text.splitlines() if line.startswith("\t")]
        self.assertTrue(recipes)
        self.assertTrue(recipes[0].lstrip("\t").startswith("@awk"))
        # Every recipe line belongs to the awk help pipeline (continuations).
        self.assertTrue(all(line.rstrip().endswith("\\") for line in recipes[:-1]))


class FacadeDeterminismTests(unittest.TestCase):
    def test_rendering_is_deterministic(self) -> None:
        self.assertEqual(render_facade("make-v1"), render_facade("make-v1"))

    def test_unsupported_facade_cannot_render(self) -> None:
        with self.assertRaisesRegex(ContractError, "unsupported facade"):
            render_facade("make-v9")

    def test_reconcile_is_one_directional_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            (root / "Makefile").write_text("drift\n", encoding="utf-8")
            repo_mk_before = (root / "Repo.mk").read_bytes()
            execution = RepositoryExecution(root)
            self.assertTrue(execution.reconcile())
            self.assertEqual("", git(root, "status", "--porcelain"))
            self.assertFalse(execution.reconcile())
            self.assertEqual("", git(root, "status", "--porcelain"))
            self.assertEqual(render_facade("make-v1"), (root / "Makefile").read_bytes())
            self.assertEqual(repo_mk_before, (root / "Repo.mk").read_bytes())

    def test_reconcile_never_writes_repo_mk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            (root / "Makefile").unlink()
            repo_mk = root / "Repo.mk"
            repo_mk.chmod(0o444)
            try:
                RepositoryExecution(root).reconcile()
            finally:
                repo_mk.chmod(0o644)
            self.assertEqual(logging_repo_mk(), repo_mk.read_text(encoding="utf-8"))

    def test_drift_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            RepositoryExecution(root).validate()
            with (root / "Makefile").open("a", encoding="utf-8") as stream:
                stream.write("\nextra:\n\t@true\n")
            with self.assertRaisesRegex(ContractError, "drift"):
                RepositoryExecution(root).validate()
            (root / "Makefile").unlink()
            with self.assertRaisesRegex(ContractError, "missing generated"):
                RepositoryExecution(root).validate()


@unittest.skipUnless(shutil.which("make"), "GNU make is not installed")
class FacadeRoutingTests(unittest.TestCase):
    """``make <phase>`` reaches ``repo-<phase>`` through a real GNU make."""

    def test_each_phase_reaches_its_repository_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            for phase in PHASES:
                with self.subTest(phase=phase):
                    result = make(root, phase)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(phase, phases_log(root)[-1])
            self.assertEqual(list(PHASES), phases_log(root))

    def test_phase_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root, repo_mk=logging_repo_mk(failing="check"))
            result = make(root, "check")
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(["check"], phases_log(root))

    def test_same_named_file_cannot_suppress_a_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            for name in ("test", "repo-test"):
                (root / name).write_text("decoy\n", encoding="utf-8")
            result = make(root, "test")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["test"], phases_log(root))

    def test_help_lists_only_the_portable_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            result = make(root, "help")
            self.assertEqual(0, result.returncode, result.stderr)
            listed = [line.split()[0] for line in result.stdout.splitlines() if line]
            self.assertEqual(["help", *PHASES], listed)

    def test_consumer_needs_no_core_runtime_or_dispatcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = pathlib.Path(tmp)
            root = sandbox / "consumer"
            root.mkdir()
            write_consumer(root)
            self.assertEqual(
                {".git", ".l9", "Makefile", "Repo.mk"},
                {path.name for path in root.iterdir()},
            )
            bin_dir = sandbox / "bin"
            bin_dir.mkdir()
            trap = sandbox / "trap.log"
            for tool in ("git", "gh", "l9", "python3", "python"):
                script = bin_dir / tool
                script.write_text(
                    f'#!/usr/bin/env bash\necho "{tool} $*" >> "{trap}"\nexit 99\n',
                    encoding="utf-8",
                )
                script.chmod(0o755)
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": str(sandbox),
            }
            for phase in PHASES:
                result = make(root, phase, env=env)
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(list(PHASES), phases_log(root))
            self.assertFalse(trap.exists(), "facade reached a forbidden tool")


# make-v2: a declared bounded test phase beside the whole suite. make-v1 has one
# test phase, and the required check runs it on every pull request, so the
# expense judge blocks any whole-tree suite there. make-v2 adds test-fast
# (repo-test-fast): the required check runs it on pull requests, merge groups
# and pushes, the judge holds it to a bounded nonempty selection, and test
# stays the whole suite.

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


class MakeV2FacadeTests(unittest.TestCase):
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


class MakeV2ContractTests(unittest.TestCase):
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


class MakeV2RunnerPhaseTests(unittest.TestCase):
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


class MakeV2JudgeTests(unittest.TestCase):
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


class MakeV2StampTests(unittest.TestCase):
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
