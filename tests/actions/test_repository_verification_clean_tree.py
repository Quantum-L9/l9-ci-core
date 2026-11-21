"""Repository verification must leave tracked files as it found them.

The analysis after ``run-repository-verification`` scans the work tree against
its baseline (CENTRAL-DIFF), and the SDK refuses a baseline over tracked
changes. A consumer whose ``make setup/validate/check/test`` rewrites a tracked
file used to surface only as an unattributed Semgrep ``incomplete`` (the
Quantum-L9/.github PR #278 run 44922638924). Now the verification step itself
fails, names every changed file, and the analysis never scans that tree.
"""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
REGISTRY_FIXTURE = (
    ROOT / "tests" / "fixtures" / "repository-registry" / "repository_registry.yaml"
)


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("l9_verify_clean_tree", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fetch_registry = REGISTRY_FIXTURE.read_text
    return module


runner = _load_runner()


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )


def repository(root: Path, files: dict[str, str]) -> None:
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "add", "--", *files)
    git(root, "commit", "--quiet", "-m", "base")


class TrackedStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_plain_directory_has_no_tracked_set(self) -> None:
        (self.root / "x.py").write_text("x\n", encoding="utf-8")
        self.assertIsNone(runner.tracked_state(self.root))

    def test_a_clean_tree_and_untracked_files_report_nothing(self) -> None:
        repository(self.root, {"a.py": "a\n"})
        (self.root / "new.py").write_text("n\n", encoding="utf-8")
        self.assertEqual({}, runner.tracked_state(self.root))

    def test_modified_and_deleted_tracked_files_are_reported(self) -> None:
        repository(self.root, {"a.py": "a\n", "b.py": "b\n"})
        (self.root / "a.py").write_text("changed\n", encoding="utf-8")
        (self.root / "b.py").unlink()
        state = runner.tracked_state(self.root)
        assert state is not None
        self.assertEqual(["a.py", "b.py"], sorted(state))
        self.assertEqual("deleted", state["b.py"])

    def test_only_what_changed_between_the_two_states_counts(self) -> None:
        before = {"pre.py": "1", "same.py": "2"}
        after = {"pre.py": "1", "same.py": "3", "new.py": "4"}
        self.assertEqual(["new.py", "same.py"], runner.tracked_changes(before, after))
        self.assertEqual(["pre.py"], runner.tracked_changes({"pre.py": "1"}, {}))
        self.assertEqual([], runner.tracked_changes(None, None))

    def test_an_unreadable_status_is_never_reported_clean(self) -> None:
        repository(self.root, {"a.py": "a\n"})
        failing = subprocess.CompletedProcess([], 128, b"", b"fatal: broken")
        real = runner._run_git

        def fake(argv: list[str], **kwargs: Any) -> Any:
            if "status" in argv:
                return failing
            return real(argv, **kwargs)

        with (
            unittest.mock.patch.object(runner, "_run_git", side_effect=fake),
            self.assertRaises(RuntimeError),
        ):
            runner.tracked_state(self.root)


class VerificationTests(unittest.TestCase):
    """main(): a contract run that rewrites a tracked file fails by name."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        repository(
            self.root,
            {
                ".l9/repo-workflow.json": '{"schema_version": 1}\n',
                "src/app.py": "print(1)\n",
                "docs/generated.md": "v1\n",
            },
        )

    def verify(
        self, effect: Any, outcome: tuple[str, str, object] = ("v2", "none", "")
    ) -> tuple[int, dict[str, str], str]:
        def contract_run(workspace: Path, contract: Path, mode: str) -> Any:
            effect(workspace)
            return outcome

        with tempfile.NamedTemporaryFile() as output:
            env = {
                "L9_REPOSITORY_WORKSPACE": str(self.root),
                "GITHUB_OUTPUT": output.name,
                "L9_REPOSITORY_IDENTITY": "Quantum-L9/example-consumer",
            }
            out, err = io.StringIO(), io.StringIO()
            with (
                unittest.mock.patch.dict(os.environ, env, clear=False),
                unittest.mock.patch.object(runner, "run_contract", contract_run),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                code = runner.main()
            outputs = dict(
                line.split("=", 1)
                for line in Path(output.name).read_text(encoding="utf-8").splitlines()
            )
        return code, outputs, out.getvalue() + err.getvalue()

    def test_a_verification_that_leaves_tracked_files_alone_passes(self) -> None:
        def build(root: Path) -> None:
            (root / "build").mkdir()
            (root / "build" / "out.txt").write_text("untracked\n", encoding="utf-8")

        code, outputs, _ = self.verify(build)
        self.assertEqual(
            (0, "pass", "none"), (code, outputs["status"], outputs["failure-kind"])
        )

    def test_a_rewritten_tracked_file_fails_the_step_by_name(self) -> None:
        def regenerate(root: Path) -> None:
            (root / "docs" / "generated.md").write_text("v2\n", encoding="utf-8")

        code, outputs, text = self.verify(regenerate)
        self.assertEqual(
            1, code, "the step fails, so the analysis never scans the tree"
        )
        self.assertEqual(
            ("fail", "contract"), (outputs["status"], outputs["failure-kind"])
        )
        self.assertIn(
            "::error file=docs/generated.md::repository verification modified", text
        )
        self.assertIn(
            "verification modified 1 tracked file(s): docs/generated.md", text
        )

    def test_a_deleted_tracked_file_is_named_too(self) -> None:
        code, _, text = self.verify(lambda root: (root / "src" / "app.py").unlink())
        self.assertEqual(1, code)
        self.assertIn("::error file=src/app.py::", text)

    def test_a_command_failure_that_also_dirties_the_tree_is_named(self) -> None:
        def broken(root: Path) -> None:
            (root / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")

        failure = ("v2", "command", RuntimeError("make test exited 2"))
        code, outputs, text = self.verify(broken, failure)
        self.assertEqual((1, "contract"), (code, outputs["failure-kind"]))
        self.assertIn("src/app.py", text)

    def test_a_file_already_dirty_before_verification_is_not_blamed(self) -> None:
        (self.root / "src" / "app.py").write_text("local edit\n", encoding="utf-8")
        code, outputs, _ = self.verify(lambda root: None)
        self.assertEqual((0, "pass"), (code, outputs["status"]))

    def test_a_command_failure_without_tree_changes_stays_typed_evidence(self) -> None:
        failure = ("v2", "command", RuntimeError("make test exited 2"))
        code, outputs, _ = self.verify(lambda root: None, failure)
        self.assertEqual(
            (0, "fail", "command"), (code, outputs["status"], outputs["failure-kind"])
        )


if __name__ == "__main__":
    unittest.main()
