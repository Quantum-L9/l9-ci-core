"""org-ci's CENTRAL-DIFF baseline step, run for real against local git history.

docs/central-diff-baseline.md: pull_request and push judge against the
evaluated revision's first parent; a merge group against
``merge_group.base_sha``, which equals the first parent only for the first
group in the queue; nightly, release and supply_chain take no baseline. A
baseline that cannot be resolved blocks the run.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
POLICY = "/runner/temp/policy.yaml"


def baseline_step() -> dict[str, object]:
    data = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    return next(
        s for s in data["jobs"]["analyze"]["steps"] if s.get("id") == "baseline"
    )


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class BaselineStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        # The remote is reached as https://github.com/<owner>/<name>.git and
        # rewritten to this local repository, so the step's fetch runs offline.
        self.remote = tmp / "github" / "Quantum-L9" / "x.git"
        self.remote.mkdir(parents=True)
        git(self.remote, "init", "-q", "-b", "main")
        git(self.remote, "config", "user.email", "test@example.invalid")
        git(self.remote, "config", "user.name", "test")
        git(self.remote, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.commits = []
        for number in range(4):
            (self.remote / "f.txt").write_text(f"{number}\n", encoding="utf-8")
            git(self.remote, "add", "f.txt")
            git(self.remote, "commit", "-q", "-m", f"c{number}")
            self.commits.append(git(self.remote, "rev-parse", "HEAD"))
        # The workspace as org-ci checks it out: the evaluated revision at depth 2.
        self.work = tmp / "work"
        self.work.mkdir()
        git(self.work, "init", "-q")
        git(
            self.work,
            "fetch",
            "-q",
            "--depth=2",
            f"file://{self.remote}",
            self.commits[-1],
        )
        git(self.work, "checkout", "-q", "--detach", "FETCH_HEAD")
        self.output = tmp / "output"
        self.rewrite = f"file://{tmp / 'github'}/"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_step(
        self, event_class: str, merge_group_base: str = "", policy: str = POLICY
    ) -> subprocess.CompletedProcess[str]:
        self.output.write_text("", encoding="utf-8")
        env = {
            "PATH": os.environ["PATH"],
            "HOME": self._tmp.name,
            "GITHUB_OUTPUT": str(self.output),
            "EVENT_CLASS": event_class,
            "POLICY": policy,
            "MERGE_GROUP_BASE": merge_group_base,
            "REPOSITORY": "Quantum-L9/x",
            "TOKEN": "example-token-not-a-secret",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"url.{self.rewrite}.insteadOf",
            "GIT_CONFIG_VALUE_0": "https://github.com/",
        }
        return subprocess.run(
            ["bash", "-c", str(baseline_step()["run"])],
            cwd=self.work,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def commit(self) -> str:
        lines = self.output.read_text(encoding="utf-8").splitlines()
        (line,) = [line for line in lines if line.startswith("commit=")]
        return line.removeprefix("commit=")

    def test_pull_request_and_push_judge_against_the_first_parent(self) -> None:
        for event in ("pull_request", "push"):
            with self.subTest(event=event):
                result = self.run_step(event)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(self.commits[-2], self.commit())

    def test_a_merge_group_judges_against_its_base_not_its_first_parent(self) -> None:
        base = self.commits[0]
        result = self.run_step("merge", merge_group_base=base)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(base, self.commit())
        self.assertIn("merge group base", result.stdout)

    def test_a_merge_class_without_a_group_base_uses_the_first_parent(self) -> None:
        result = self.run_step("merge")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.commits[-2], self.commit())

    def test_an_unresolvable_merge_group_base_blocks(self) -> None:
        for base in ("abc1234", "G" * 40, "f" * 40):
            with self.subTest(base=base):
                result = self.run_step("merge", merge_group_base=base)
                self.assertEqual(1, result.returncode)
                self.assertIn("::error::no baseline", result.stdout)

    def test_other_classes_and_no_policy_take_no_baseline(self) -> None:
        for event, policy in (
            ("nightly", POLICY),
            ("release", POLICY),
            ("supply_chain", POLICY),
            ("pull_request", ""),
            ("merge", ""),
        ):
            with self.subTest(event=event, policy=policy):
                result = self.run_step(
                    event, merge_group_base=self.commits[0], policy=policy
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", self.commit())

    def test_the_baseline_is_resolved_before_any_consumer_code_runs(self) -> None:
        data = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        ids = [s.get("id") for s in data["jobs"]["analyze"]["steps"]]
        self.assertLess(ids.index("baseline"), ids.index("repository_verify"))

    def test_the_baseline_survives_a_consumer_reshallowing_the_checkout(self) -> None:
        """Seen on Cursor-Governance (#328): after its make ran, HEAD^1 was gone."""
        result = self.run_step("pull_request")
        self.assertEqual(0, result.returncode, result.stderr)
        baseline = self.commit()
        self.assertEqual(
            baseline, git(self.work, "rev-parse", "refs/l9/central-diff-baseline")
        )
        # What a consumer can do to the checkout: mark HEAD itself shallow.
        head = git(self.work, "rev-parse", "HEAD")
        shallow = Path(git(self.work, "rev-parse", "--git-path", "shallow"))
        if not shallow.is_absolute():
            shallow = self.work / shallow
        shallow.write_text(head + "\n", encoding="utf-8")
        failed = subprocess.run(
            ["git", "-C", str(self.work), "rev-parse", "--verify", "--quiet", "HEAD^1"],
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual(
            baseline, git(self.work, "rev-parse", f"{baseline}^{{commit}}")
        )

    def test_the_merge_group_base_comes_from_the_event(self) -> None:
        env = baseline_step()["env"]
        self.assertEqual(
            "${{ github.event.merge_group.base_sha }}", env["MERGE_GROUP_BASE"]
        )
        self.assertEqual("${{ github.repository }}", env["REPOSITORY"])
        self.assertEqual("${{ github.token }}", env["TOKEN"])


if __name__ == "__main__":
    unittest.main()
