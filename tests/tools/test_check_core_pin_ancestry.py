"""A Core self-pin on a required path must name a commit ``main`` contains.

The checks run against a throwaway git repository with a known graph: two
commits on ``main`` and one commit on a branch that ``main`` never merged (what
a squash merge leaves behind). The live check against Core's own history runs
in self-ci.yml's Repository contract job.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_core_pin_ancestry as guard  # noqa: E402

PIN = "        uses: Quantum-L9/l9-ci-core/.github/actions/{name}@{ref}\n"


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(repository: Path, message: str) -> str:
    git(
        repository,
        "-c",
        "user.name=core",
        "-c",
        "user.email=core@invalid",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        message,
    )
    return git(repository, "rev-parse", "HEAD")


class AncestryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch=main")
        self.base = commit(self.repo, "base")
        git(self.repo, "checkout", "--quiet", "-b", "feature")
        self.orphan = commit(self.repo, "branch commit a squash merge leaves behind")
        git(self.repo, "checkout", "--quiet", "main")
        self.head = commit(self.repo, "squash merge of feature")
        self.ancestry = guard.git_ancestry(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def workflow(self, *pins: tuple[str, str]) -> Path:
        path = self.root / "org-ci.yml"
        body = "jobs:\n  analyze:\n    steps:\n" + "".join(
            "      - name: step\n" + PIN.format(name=name, ref=ref)
            for name, ref in pins
        )
        path.write_text(body, encoding="utf-8")
        return path

    def test_ancestors_of_main_pass(self) -> None:
        path = self.workflow(("provision-sdk", self.base), ("invoke-sdk", self.head))
        self.assertEqual([], guard.problems([path], "main", self.ancestry))

    def test_an_orphaned_branch_commit_fails(self) -> None:
        path = self.workflow(("provision-sdk", self.head), ("verify", self.orphan))
        found = guard.problems([path], "main", self.ancestry)
        self.assertEqual(1, len(found))
        self.assertIn(f"{self.orphan} is not an ancestor of main", found[0])
        self.assertIn("org-ci.yml:", found[0])

    def test_an_unknown_commit_is_never_assumed_safe(self) -> None:
        path = self.workflow(("verify", "0" * 40))
        found = guard.problems([path], "main", self.ancestry)
        self.assertEqual(1, len(found))
        self.assertIn("not a commit in this clone", found[0])

    def test_floating_and_short_references_fail(self) -> None:
        for ref in ("main", "v1", self.head[:12], self.head.upper()):
            with self.subTest(ref=ref):
                path = self.workflow(("verify", ref))
                found = guard.problems([path], "main", self.ancestry)
                self.assertEqual(1, len(found))
                self.assertIn("is not a full lowercase commit SHA", found[0])

    def test_reusable_workflow_pins_are_checked_too(self) -> None:
        path = self.root / "caller.yml"
        path.write_text(
            "jobs:\n  call:\n    uses: Quantum-L9/l9-ci-core/.github/workflows/"
            f"normalize-semgrep-report.yml@{self.orphan}\n",
            encoding="utf-8",
        )
        self.assertEqual(1, len(guard.problems([path], "main", self.ancestry)))

    def test_an_unreadable_workflow_fails(self) -> None:
        found = guard.problems([self.root / "absent.yml"], "main", self.ancestry)
        self.assertEqual(1, len(found))
        self.assertIn("unreadable", found[0])

    def test_each_commit_is_queried_once(self) -> None:
        calls: list[str] = []

        def counting(sha: str, main: str) -> int:
            calls.append(sha)
            return 0

        path = self.workflow(("a", self.head), ("b", self.head), ("c", self.base))
        self.assertEqual([], guard.problems([path], "main", counting))
        self.assertEqual([self.head, self.base], calls)


class CommandTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = guard.main(argv)
        return code, out.getvalue()

    def test_an_option_like_main_ref_is_refused(self) -> None:
        self.assertEqual(2, self.run_main(["--main=--all"])[0])

    def test_reports_every_problem_and_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "--quiet", "--initial-branch=main")
            commit(repo, "only")
            code, printed = self.run_main(["--main", "main", "--repository", str(repo)])
        # Core's real pins are not commits of this throwaway repository.
        self.assertEqual(1, code)
        self.assertIn("::error::org-ci.yml:", printed)
        self.assertIn("not a commit in this clone", printed)


class RequiredPathTests(unittest.TestCase):
    def test_required_workflows_exist_and_pin_by_full_sha(self) -> None:
        for name in guard.REQUIRED_WORKFLOWS:
            with self.subTest(workflow=name):
                text = (guard.WORKFLOWS / name).read_text(encoding="utf-8")
                pins = guard.self_pins(text)
                self.assertTrue(pins, f"{name} has no Core self-pin to guard")
                for number, ref in pins:
                    self.assertRegex(ref, guard.FULL_SHA, f"{name}:{number}")

    def test_repository_contract_job_runs_the_guard_against_main(self) -> None:
        workflow = yaml.safe_load(
            (guard.WORKFLOWS / "self-ci.yml").read_text(encoding="utf-8")
        )
        steps = workflow["jobs"]["repository-contract"]["steps"]
        names = [step.get("name") for step in steps]
        self.assertLess(
            names.index("Checkout immutable event revision"),
            names.index("Core self-pins are ancestors of main"),
        )
        step = steps[names.index("Core self-pins are ancestors of main")]
        run = str(step["run"])
        self.assertIn("fetch --unshallow --filter=tree:0 --no-tags origin main", run)
        self.assertIn("python3 tools/check_core_pin_ancestry.py --main FETCH_HEAD", run)
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("if", step)


if __name__ == "__main__":
    unittest.main()
