"""Repository verification fails a tree with an unresolved merge conflict.

The check runs in ``run-repository-verification`` before any contract
decision, so it covers every governed repository: with a V2 contract, a V1
contract, or none. Only git's complete marker block counts; a lone
``=======`` (a text rule) and quoted markers do not.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
REGISTRY_FIXTURE = (
    ROOT / "tests" / "fixtures" / "repository-registry" / "repository_registry.yaml"
)

CONFLICT = "a = 1\n<<<<<<< HEAD\nb = 2\n=======\nb = 3\n>>>>>>> feature\nc = 4\n"
DIFF3 = "<<<<<<< ours\nx\n||||||| base\nw\n=======\ny\n>>>>>>> theirs\n"


def _load_runner():
    spec = importlib.util.spec_from_file_location("l9_verify_markers", RUNNER)
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


class MarkerBlockTests(unittest.TestCase):
    def test_a_complete_block_is_reported_at_its_opening_line(self) -> None:
        self.assertEqual([2], runner.conflict_marker_blocks(CONFLICT))

    def test_a_diff3_block_is_reported(self) -> None:
        self.assertEqual([1], runner.conflict_marker_blocks(DIFF3))

    def test_every_block_in_a_file_is_reported(self) -> None:
        self.assertEqual([2, 9], runner.conflict_marker_blocks(CONFLICT + CONFLICT))

    def test_text_that_only_resembles_markers_is_not_reported(self) -> None:
        for text in (
            "Title\n=======\n",  # a setext-style rule
            "<<<<<<< only an opener\n",
            "=======\n>>>>>>> only a closer\n",
            "  <<<<<<< HEAD\n  =======\n  >>>>>>> b\n",  # indented, e.g. a doc sample
            "<<<<<<<<< nine\n=======\n>>>>>>> b\n",  # not seven
            "<<<<<<< a\n======= trailing\n>>>>>>> b\n",
        ):
            with self.subTest(text=text):
                self.assertEqual([], runner.conflict_marker_blocks(text))


class TreeScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tracked_conflicts_are_found_and_untracked_ignored(self) -> None:
        git(self.root, "init", "--quiet")
        (self.root / "app.py").write_text(CONFLICT, encoding="utf-8")
        (self.root / "notes.txt").write_text(CONFLICT, encoding="utf-8")
        git(self.root, "add", "app.py")
        self.assertEqual([("app.py", 2)], runner.conflict_markers(self.root))

    def test_binary_files_and_the_sdk_runtime_are_not_scanned(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\0" + CONFLICT.encode())
        runtime = self.root / ".l9" / "runtime" / "sdk"
        runtime.mkdir(parents=True)
        (runtime / "vendored.py").write_text(CONFLICT, encoding="utf-8")
        self.assertEqual([], runner.conflict_markers(self.root))

    def test_a_plain_directory_is_scanned_whole(self) -> None:
        nested = self.root / "pkg"
        nested.mkdir()
        (nested / "mod.py").write_text(DIFF3, encoding="utf-8")
        self.assertEqual([("pkg/mod.py", 1)], runner.conflict_markers(self.root))

    def test_an_unreadable_work_tree_is_never_reported_clean(self) -> None:
        git(self.root, "init", "--quiet")
        inside = subprocess.CompletedProcess([], 0, stdout="true\n", stderr="")
        failed = subprocess.CompletedProcess(
            [], 128, stdout=b"", stderr=b"broken index"
        )
        with (
            unittest.mock.patch.object(
                runner, "_run_git", side_effect=[inside, failed]
            ),
            self.assertRaises(RuntimeError),
        ):
            runner.conflict_markers(self.root)


class VerificationOutcomeTests(unittest.TestCase):
    def verify(self, files: dict[str, str]) -> tuple[str, str]:
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            root = Path(tmp)
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            env = {
                "L9_REPOSITORY_WORKSPACE": tmp,
                "GITHUB_OUTPUT": output.name,
                "L9_REPOSITORY_IDENTITY": "Quantum-L9/example-consumer",
            }
            with (
                unittest.mock.patch.dict(os.environ, env, clear=False),
                unittest.mock.patch.object(
                    runner.subprocess, "run", side_effect=AssertionError("no phase")
                ),
            ):
                self.assertEqual(0, runner.main())
            outputs = dict(
                line.split("=", 1)
                for line in Path(output.name).read_text(encoding="utf-8").splitlines()
            )
            return outputs["status"], outputs["failure-kind"]

    def test_a_conflict_fails_a_repository_with_no_contract(self) -> None:
        self.assertEqual(("fail", "contract"), self.verify({"src/x.py": CONFLICT}))

    def test_a_conflict_fails_before_any_phase_of_a_contract_runs(self) -> None:
        files = {".l9/repo-workflow.json": '{"schema_version": 1}\n', "x.py": DIFF3}
        self.assertEqual(("fail", "contract"), self.verify(files))

    def test_a_clean_tree_without_a_contract_is_still_not_applicable(self) -> None:
        self.assertEqual(
            ("not_applicable", "none"), self.verify({"x.py": "print(1)\n"})
        )


if __name__ == "__main__":
    unittest.main()
