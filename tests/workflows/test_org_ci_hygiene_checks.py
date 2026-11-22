"""no-bytecode and sha-pins: org-wide checks of what a change adds (2026-11-21).

Operator-directed correction (docs/hygiene-checks.md): two checks that lived
only as Quantum-L9/.github scripts become Core org checks. These tests run
each check's own script against real two-commit repositories.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
SHA = "34e114876b0b11c390a56381ad16ebd13914f8d5"


def step(job: str, identifier: str) -> dict[str, Any]:
    steps = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))["jobs"][job]["steps"]
    [found] = [s for s in steps if s.get("id") == identifier]
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
        git(root, "add", "-f", "--", name)
    git(root, "commit", "-q", "--allow-empty", "-m", "c")
    return git(root, "rev-parse", "HEAD")


def run_check(
    job: str,
    identifier: str,
    base_files: dict[str, str],
    change: dict[str, str],
    **env: str,
) -> tuple[subprocess.CompletedProcess[str], str, Path]:
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        root = temp / "repo"
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        base = commit(root, base_files)
        head = commit(root, change)
        summary = temp / "summary"
        summary.touch()
        sarif = temp / "out.sarif"
        completed = subprocess.run(
            ["bash", "-c", step(job, identifier)["run"]],
            cwd=root,
            env={
                "PATH": os.environ["PATH"],
                "HOME": tmp,
                "GITHUB_STEP_SUMMARY": str(summary),
                "EVENT_NAME": "pull_request",
                "PR_BASE": base,
                "EVENT_SHA": head,
                "FINDINGS_FAIL": "true",
                "SARIF": "",
                **{k: v.replace("@SARIF", str(sarif)) for k, v in env.items()},
            },
            capture_output=True,
            text=True,
            check=False,
        )
        document = sarif.read_text(encoding="utf-8") if sarif.exists() else ""
        return completed, summary.read_text(encoding="utf-8"), Path(document or "-")


class WiringTests(unittest.TestCase):
    def test_two_read_only_checks_beside_the_others(self) -> None:
        jobs = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))["jobs"]
        for job in ("no-bytecode", "sha-pins"):
            with self.subTest(job=job):
                self.assertEqual(job, jobs[job]["name"])
                self.assertEqual({"contents": "read"}, jobs[job]["permissions"])
                self.assertNotIn("needs", jobs[job])
                self.assertEqual(jobs["gitleaks"]["if"], jobs[job]["if"])


class BytecodeTests(unittest.TestCase):
    def check(self, base: dict[str, str], change: dict[str, str], **env: str):
        return run_check("no-bytecode", "no_bytecode", base, change, **env)

    def test_added_bytecode_fails_annotated(self) -> None:
        completed, summary, _ = self.check(
            {"a.py": ""}, {"pkg/__pycache__/a.cpython-312.pyc": "x", "b.pyo": "x"}
        )
        self.assertEqual(1, completed.returncode)
        self.assertIn(
            "::error file=pkg/__pycache__/a.cpython-312.pyc,line=1,title=no-bytecode::",
            completed.stdout,
        )
        self.assertIn("::error file=b.pyo,line=1,", completed.stdout)
        self.assertIn("findings: 2", summary)

    def test_bytecode_the_change_does_not_touch_is_not_judged(self) -> None:
        completed, summary, _ = self.check({"old.pyc": "x"}, {"a.py": "print(1)\n"})
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("pass: no tracked bytecode in 1 file(s)", summary)

    def test_with_no_base_every_file_is_reported_and_passes(self) -> None:
        completed, summary, _ = self.check(
            {"old.pyc": "x"}, {}, EVENT_NAME="workflow_dispatch"
        )
        self.assertEqual(0, completed.returncode)
        self.assertIn("::warning file=old.pyc,line=1,", completed.stdout)
        self.assertIn("(reported: no base): findings: 1 (every tracked file)", summary)

    def test_a_base_outside_the_clone_fails_by_name(self) -> None:
        completed, _, _ = self.check({}, {}, PR_BASE="1" * 40)
        self.assertEqual(2, completed.returncode)
        self.assertIn("::error title=no-bytecode::the event's base", completed.stdout)

    def test_the_nightly_mode_writes_sarif_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            target = Path(out, "x.sarif")
            completed, _, _ = self.check(
                {"old.pyc": "x"},
                {},
                EVENT_NAME="schedule",
                FINDINGS_FAIL="false",
                SARIF=str(target),
            )
            sarif = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(0, completed.returncode)
        [result] = sarif["runs"][0]["results"]
        self.assertEqual("no-bytecode", result["ruleId"])
        self.assertEqual(
            "old.pyc",
            result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
        )


WORKFLOW = """\
on: push
jobs:
  j:
    runs-on: ubuntu-24.04
    steps:
{steps}
"""


def workflow(*uses: str) -> str:
    return WORKFLOW.format(steps="\n".join(f"      - uses: {u}" for u in uses))


class PinTests(unittest.TestCase):
    def check(self, base: dict[str, str], change: dict[str, str], **env: str):
        return run_check("sha-pins", "sha_pins", base, change, **env)

    def test_a_floating_ref_in_a_changed_workflow_fails_at_its_line(self) -> None:
        completed, summary, _ = self.check(
            {},
            {
                ".github/workflows/ci.yml": workflow(
                    f"actions/checkout@{SHA}", "actions/setup-python@v5"
                )
            },
        )
        self.assertEqual(1, completed.returncode)
        self.assertIn(
            "::error file=.github/workflows/ci.yml,line=7,title=sha-pins::floating ref 'v5' on 'actions/setup-python'",
            completed.stdout,
        )
        self.assertIn("findings: 1", summary)

    def test_shas_frozen_core_tags_and_local_actions_pass(self) -> None:
        completed, summary, _ = self.check(
            {},
            {
                ".github/workflows/ci.yml": workflow(
                    f"actions/checkout@{SHA}  # v4.2.0",
                    f"'actions/cache@{SHA}'",
                    "Quantum-L9/l9-ci-core/.github/workflows/security.yml@v1",
                    "Quantum-L9/l9-ci-core@v2.0.0",
                    "./.github/actions/local",
                )
            },
        )
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("pass: 4 uses: reference(s) in 1 file(s) pinned", summary)

    def test_docker_needs_a_digest_and_a_ref_is_required(self) -> None:
        completed, _, _ = self.check(
            {},
            {
                "tools/act/action.yml": "runs:\n  using: composite\n  steps:\n"
                "    - uses: docker://alpine:3.20\n"
                "    - uses: actions/checkout\n"
                f"    - uses: docker://alpine@sha256:{'a' * 64}\n"
            },
        )
        self.assertEqual(1, completed.returncode)
        self.assertIn("file=tools/act/action.yml,line=4,", completed.stdout)
        self.assertIn("not pinned by image digest", completed.stdout)
        self.assertIn("file=tools/act/action.yml,line=5,", completed.stdout)
        self.assertNotIn("line=6,", completed.stdout)

    def test_an_untouched_workflow_is_not_judged(self) -> None:
        completed, summary, _ = self.check(
            {".github/workflows/old.yml": workflow("actions/checkout@main")},
            {"README.md": "x\n"},
        )
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("pass: no changed workflow or action files", summary)

    def test_commented_uses_lines_are_ignored(self) -> None:
        completed, _, _ = self.check(
            {},
            {
                ".github/workflows/ci.yml": workflow(f"actions/checkout@{SHA}")
                + "      # - uses: x/y@main\n"
            },
        )
        self.assertEqual(0, completed.returncode, completed.stdout)

    def test_a_repository_without_workflows_is_not_applicable_with_no_base(
        self,
    ) -> None:
        completed, summary, _ = self.check(
            {"a.py": ""}, {}, EVENT_NAME="workflow_dispatch"
        )
        self.assertEqual(0, completed.returncode)
        self.assertIn("not applicable: no workflow or action files", summary)


if __name__ == "__main__":
    unittest.main()
