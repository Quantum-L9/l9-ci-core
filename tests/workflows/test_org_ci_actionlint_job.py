"""actionlint is one org-wide check, judging changed workflow files (2026-11-21).

Operator decision (docs/actionlint-check.md). The `actionlint` job in
org-ci.yml is blocking and always reports. These tests run its scope and
verdict scripts against real repositories, bind its version and checksum to
Core's toolchain lock, and, where an actionlint executable is installed, run
the whole job end to end.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
LOCK = ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"
LOCKS = ROOT / ".github" / "actions" / "install-consumer-ci" / "locks"
GOOD = "on: push\njobs:\n  j:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n"
BAD = (
    "on: push\njobs:\n  j:\n    runs-on: ubuntu-24.04\n    steps:\n"
    "      - run: echo ${{ github.no_such_property }}\n"
)


def job() -> dict[str, Any]:
    return yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))["jobs"]["actionlint"]


def step(identifier: str) -> dict[str, Any]:
    [found] = [s for s in job()["steps"] if s.get("id") == identifier]
    return found


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(root),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    ).stdout.strip()


def commit(root: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
        git(root, "add", "--", name)
    git(root, "commit", "-q", "--allow-empty", "-m", "c")
    return git(root, "rev-parse", "HEAD")


def run_step(
    identifier: str, cwd: Path, temp: Path, env: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
    output = temp / f"output-{identifier}"
    summary = temp / "summary"
    output.write_text("", encoding="utf-8")
    summary.touch()
    completed = subprocess.run(
        ["bash", "-c", step(identifier)["run"]],
        cwd=cwd,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(temp),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_TEMP": str(temp),
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    values = dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return completed, values, summary.read_text(encoding="utf-8")


class Repository:
    """A two-commit repository: base files, then the change."""

    def __init__(self, base: dict[str, str], change: dict[str, str]) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.temp = Path(self._tmp.name)
        self.root = self.temp / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        self.base = commit(self.root, base)
        self.head = commit(self.root, change)

    def scope(
        self, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
        return run_step(
            "actionlint_scope",
            self.root,
            self.temp,
            {
                "EVENT_NAME": "pull_request",
                "PR_BASE": self.base,
                "EVENT_SHA": self.head,
                **env,
            },
        )

    def files(self, values: dict[str, str]) -> list[str]:
        return Path(values["files"]).read_text(encoding="utf-8").split()

    def close(self) -> None:
        self._tmp.cleanup()


class WiringTests(unittest.TestCase):
    def test_one_read_only_blocking_check_named_actionlint(self) -> None:
        jobs = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))["jobs"]
        self.assertEqual("actionlint", jobs["actionlint"]["name"])
        self.assertEqual({"contents": "read"}, jobs["actionlint"]["permissions"])
        self.assertNotIn("needs", jobs["actionlint"])
        self.assertEqual(jobs["gitleaks"]["if"], jobs["actionlint"]["if"])
        self.assertEqual(
            "true", step("actionlint_check")["env"]["ACTIONLINT_FINDINGS_FAIL"]
        )
        self.assertEqual("", step("actionlint_check")["env"]["ACTIONLINT_SARIF"])

    def test_version_and_checksum_are_the_toolchain_locks(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        version = lock["actionlint"]
        binary = json.loads(
            (LOCKS / f"actionlint-{version}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(version, binary["version"])
        script = step("actionlint_install")["run"]
        self.assertIn(f'ACTIONLINT_VERSION="{version}"', script)
        self.assertIn(
            f'ACTIONLINT_SHA256="{binary["platforms"]["linux-x64"]["sha256"]}"', script
        )
        self.assertIn(
            binary["platforms"]["linux-x64"]["asset"].replace(
                version, "${ACTIONLINT_VERSION}"
            ),
            script,
        )
        self.assertIn('[ "${actual}" != "${ACTIONLINT_SHA256}" ]', script)

    def test_unpinned_integrations_are_off(self) -> None:
        self.assertIn("-shellcheck= -pyflakes=", step("actionlint_check")["run"])


class ScopeTests(unittest.TestCase):
    def test_only_the_changed_workflow_files_are_judged(self) -> None:
        repo = Repository(
            {".github/workflows/a.yml": GOOD, ".github/workflows/b.yml": GOOD},
            {".github/workflows/b.yml": GOOD + "# edit\n", "src/x.py": ""},
        )
        try:
            completed, values, _ = repo.scope()
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("true", values["applies"])
            self.assertEqual([".github/workflows/b.yml"], repo.files(values))
        finally:
            repo.close()

    def test_a_changed_action_definition_judges_every_workflow(self) -> None:
        repo = Repository(
            {".github/workflows/a.yml": GOOD, ".github/workflows/b.yaml": GOOD},
            {"actions/x/action.yml": "runs: {using: composite, steps: []}\n"},
        )
        try:
            _, values, _ = repo.scope()
            self.assertEqual(
                [".github/workflows/a.yml", ".github/workflows/b.yaml"],
                repo.files(values),
            )
            self.assertIn("an action definition changed", values["scope"])
        finally:
            repo.close()

    def test_no_changed_workflow_file_is_a_success_that_says_so(self) -> None:
        repo = Repository({".github/workflows/a.yml": GOOD}, {"README.md": "x\n"})
        try:
            completed, values, summary = repo.scope()
            self.assertEqual(0, completed.returncode)
            self.assertEqual("false", values["applies"])
            self.assertIn("pass: no changed workflow or action files", summary)
        finally:
            repo.close()

    def test_no_workflow_files_is_not_applicable(self) -> None:
        repo = Repository({"README.md": "x\n"}, {"src/x.py": ""})
        try:
            _, values, summary = repo.scope()
            self.assertEqual("false", values["applies"])
            self.assertIn("not applicable: no workflow files", summary)
        finally:
            repo.close()

    def test_an_event_without_a_base_judges_every_workflow(self) -> None:
        repo = Repository({".github/workflows/a.yml": GOOD}, {"README.md": "x\n"})
        try:
            _, values, _ = repo.scope(EVENT_NAME="workflow_dispatch")
            self.assertEqual("every workflow", values["scope"])
            self.assertEqual([".github/workflows/a.yml"], repo.files(values))
        finally:
            repo.close()

    def test_a_base_outside_the_clone_fails_by_name(self) -> None:
        repo = Repository({".github/workflows/a.yml": GOOD}, {})
        try:
            completed, _, _ = repo.scope(PR_BASE="1" * 40)
            self.assertEqual(1, completed.returncode)
            self.assertIn(
                "::error title=actionlint::the event's base", completed.stdout
            )
        finally:
            repo.close()


class CheckTests(unittest.TestCase):
    """The run step with a stand-in actionlint that prints a fixed report."""

    FINDING = {
        "message": 'property "no_such_property" is not defined',
        "filepath": ".github/workflows/b.yml",
        "line": 6,
        "column": 22,
        "kind": "expression",
    }

    def check(
        self, report: list[dict[str, Any]], exit_code: int, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], str, Path]:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            fake = temp / "actionlint"
            fake.write_text(
                f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(report)}\nEOF\nexit {exit_code}\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            files = temp / "files"
            files.write_text(".github/workflows/b.yml\n", encoding="utf-8")
            completed, _, summary = run_step(
                "actionlint_check",
                temp,
                temp,
                {
                    "ACTIONLINT": str(fake),
                    "FILES": str(files),
                    "SCOPE": "the workflow files a..b changes",
                    "ACTIONLINT_FINDINGS_FAIL": "true",
                    "ACTIONLINT_SARIF": "",
                    **env,
                },
            )
            sarif = temp / "out.sarif"
            return completed, summary, sarif

    def test_a_finding_fails_annotated_at_file_line_and_column(self) -> None:
        completed, summary, _ = self.check([self.FINDING], 1)
        self.assertEqual(1, completed.returncode)
        self.assertIn(
            "::error file=.github/workflows/b.yml,line=6,col=22,title=actionlint expression::"
            'property "no_such_property" is not defined',
            completed.stdout,
        )
        self.assertIn("findings: 1 in 1 workflow file(s)", summary)

    def test_a_clean_run_passes(self) -> None:
        completed, summary, _ = self.check([], 0)
        self.assertEqual(0, completed.returncode)
        self.assertIn("pass: 1 workflow file(s)", summary)

    def test_an_actionlint_failure_is_never_a_pass(self) -> None:
        completed, _, _ = self.check([], 3)
        self.assertEqual(3, completed.returncode)
        self.assertIn("the check did not complete", completed.stdout)

    def test_the_nightly_mode_writes_sarif_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            target = Path(out, "actionlint.sarif")
            completed, summary, _ = self.check(
                [self.FINDING],
                1,
                ACTIONLINT_FINDINGS_FAIL="false",
                ACTIONLINT_SARIF=str(target),
            )
            sarif = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(0, completed.returncode)
        self.assertIn("(nightly, reported)", summary)
        [result] = sarif["runs"][0]["results"]
        self.assertEqual("expression", result["ruleId"])
        location = result["locations"][0]["physicalLocation"]
        self.assertEqual(".github/workflows/b.yml", location["artifactLocation"]["uri"])
        self.assertEqual({"startLine": 6, "startColumn": 22}, location["region"])


class EndToEndTests(unittest.TestCase):
    """The scope and run steps with a real actionlint.

    Skip condition: no ``actionlint`` executable on PATH. The job downloads
    its locked release at run time, which a unit test cannot; the scripts are
    tested above with a stand-in, and this runs wherever actionlint is
    installed.
    """

    def test_a_pull_request_breaking_a_workflow_fails_and_an_untouched_one_is_not_judged(
        self,
    ) -> None:
        executable = shutil.which("actionlint")
        if executable is None:
            self.skipTest("no actionlint executable on PATH (see the docstring)")
        repo = Repository(
            {".github/workflows/old.yml": BAD, ".github/workflows/b.yml": GOOD},
            {".github/workflows/b.yml": BAD},
        )
        try:
            _, scope, _ = repo.scope()
            completed, _, summary = run_step(
                "actionlint_check",
                repo.root,
                repo.temp,
                {
                    "ACTIONLINT": executable,
                    "FILES": scope["files"],
                    "SCOPE": scope["scope"],
                    "ACTIONLINT_FINDINGS_FAIL": "true",
                    "ACTIONLINT_SARIF": "",
                },
            )
        finally:
            repo.close()
        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("file=.github/workflows/b.yml,line=6,", completed.stdout)
        self.assertNotIn("old.yml", completed.stdout, "an untouched file is not judged")
        self.assertIn("findings: 1 in 1 workflow file(s)", summary)


if __name__ == "__main__":
    unittest.main()
