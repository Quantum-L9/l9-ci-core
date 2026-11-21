"""security.yml's pip-audit audits the consumer's full resolved lock (SEC-LOCKSET).

Moving a consumer's dependency audit into Core must not shrink it: a uv
project is audited as the whole of uv.lock (every group, extra and workspace
package), a requirements-only project as requirements.txt, and a Python project
with neither is incomplete and fails. On a pull request, merge group or push the
audit runs only when the event's range changes a dependency manifest or lock
(row 23); any other event, or an unreadable range, audits.

The step's own script runs here with stub executables that record their
arguments, against real git histories, so the behaviour is observed rather than
read off the YAML.
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
SECURITY = ROOT / ".github" / "workflows" / "security.yml"
LOCK = ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"
STEP = "Python dependency audit (pip-audit)"


def _script() -> str:
    document = yaml.safe_load(SECURITY.read_text(encoding="utf-8"))
    [step] = [s for s in document["jobs"]["security"]["steps"] if s.get("name") == STEP]
    return str(step["run"])


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class Project:
    """A consumer checkout with a two-commit history and recording stubs."""

    def __init__(self, root: Path, files: dict[str, str]) -> None:
        self.root = root / "project"
        self.root.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "calls.log"
        self.output = root / "output"
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.invalid")
        _git(self.root, "config", "user.name", "t")
        for name, text in files.items():
            (self.root / name).write_text(text, encoding="utf-8")
        (self.root / "README.md").write_text("a\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")

    def commit(self, name: str, text: str) -> str:
        (self.root / name).write_text(text, encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", f"change {name}")
        return _git(self.root, "rev-parse", "HEAD")

    def stub(self, name: str, code: int = 0) -> None:
        path = self.bin / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "{name} $*" >> "{self.log}"\n'
            f"exit {code}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def run(self, env: dict[str, str]) -> tuple[int, str, list[str]]:
        self.output.write_text("", encoding="utf-8")
        if self.log.exists():
            self.log.unlink()
        completed = subprocess.run(
            ["bash", "-c", _script()],
            env={
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(self.output),
                "RUNNER_TEMP": str(self.root.parent),
                "L9_CI_INSTALL_COMMAND": "",
                **env,
            },
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        calls = (
            self.log.read_text(encoding="utf-8").splitlines()
            if self.log.exists()
            else []
        )
        text = completed.stdout + completed.stderr + self.output.read_text("utf-8")
        return completed.returncode, text, calls


EXPORTED = "anyio==4.6.2\nhttpx[http2]==0.28.1\n# a comment\nsniffio==1.3.1\n"


def exporting_uv(project: Project, code: int = 0) -> None:
    """A uv stub that records its call and, like `uv export`, writes the
    requirements file named by --output-file (three pinned packages)."""
    path = project.bin / "uv"
    path.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "uv $*" >> "{project.log}"\n'
        f"[ {code} -eq 0 ] || exit {code}\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--output-file" ]; then\n'
        f"    printf '%s' '{EXPORTED}' > \"$2\"\n"
        "  fi\n"
        "  shift\n"
        "done\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _audits(calls: list[str]) -> list[str]:
    return [call for call in calls if call.startswith("pip-audit ")]


class LocksetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def project(self, files: dict[str, str]) -> Project:
        project = Project(Path(self.tmp.name), files)
        for tool in ("python", "pip", "pip-audit"):
            project.stub(tool)
        exporting_uv(project)
        return project

    def test_a_uv_project_audits_the_whole_exported_lock(self) -> None:
        project = self.project({"pyproject.toml": "[project]\n", "uv.lock": "x\n"})
        code, text, calls = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(0, code, text)
        [export] = [call for call in calls if call.startswith("uv export")]
        for flag in (
            "--frozen",
            "--all-groups",
            "--all-extras",
            "--all-packages",
            "--no-emit-workspace",
            "--format requirements.txt",
        ):
            self.assertIn(flag, export)
        exported = export.split("--output-file ", 1)[1].split()[0]
        [audit] = _audits(calls)
        self.assertIn(f"--no-deps -r {exported}", audit)
        self.assertIn("outcome=executed_pass", text)

    def test_the_log_names_the_export_and_the_audited_count(self) -> None:
        project = self.project({"pyproject.toml": "[project]\n", "uv.lock": "x\n"})
        code, text, _ = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(0, code, text)
        self.assertIn(
            "pip-audit: uv export --frozen --all-packages --no-emit-workspace"
            " --no-hashes --format requirements.txt --all-groups --all-extras (all)",
            text,
        )
        self.assertIn("pip-audit: auditing 3 pinned package(s) from", text)
        self.assertIn("pip-audit: 3 pinned package(s) audited in all", text)

    def test_an_export_that_writes_nothing_is_incomplete(self) -> None:
        project = self.project({"pyproject.toml": "[project]\n", "uv.lock": "x\n"})
        project.stub("uv")
        code, text, calls = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertNotEqual(0, code)
        self.assertIn("outcome=not_executed (the locked set was not written)", text)
        self.assertEqual([], _audits(calls))

    def test_the_lock_outranks_requirements_txt(self) -> None:
        project = self.project(
            {
                "pyproject.toml": "[project]\n",
                "uv.lock": "x\n",
                "requirements.txt": "a\n",
            }
        )
        _, _, calls = project.run({"EVENT_NAME": "schedule"})
        [audit] = _audits(calls)
        self.assertNotIn("-r requirements.txt", audit)

    def test_a_requirements_project_audits_its_requirements(self) -> None:
        project = self.project({"requirements.txt": "a==1\n"})
        code, _, calls = project.run({"EVENT_NAME": "schedule"})
        self.assertEqual(0, code)
        self.assertEqual(["pip-audit --desc on -r requirements.txt"], _audits(calls))

    def test_an_unexportable_lock_is_incomplete_and_fails(self) -> None:
        project = self.project({"pyproject.toml": "[project]\n", "uv.lock": "x\n"})
        project.stub("uv", 2)
        code, text, calls = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(1, code)
        self.assertIn("outcome=not_executed (uv.lock could not be exported)", text)
        self.assertEqual([], _audits(calls))

    def test_a_python_project_with_no_lock_is_incomplete_and_fails(self) -> None:
        project = self.project({"pyproject.toml": "[project]\n"})
        code, text, calls = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(1, code)
        self.assertIn("outcome=not_executed (no lock file)", text)
        self.assertEqual([], _audits(calls))

    def test_conflicting_selections_are_exported_and_audited_one_by_one(self) -> None:
        # [tool.uv].conflicts makes --all-extras/--all-groups unresolvable by
        # design; every selection is still exported and audited.
        pyproject = (
            "[project]\nname = 'p'\n"
            "[project.optional-dependencies]\ncpu = ['a']\ngpu = ['b']\n"
            "[dependency-groups]\ndev = ['c']\n"
            "[tool.uv]\nconflicts = [[{extra = 'cpu'}, {extra = 'gpu'}]]\n"
        )
        project = self.project({"pyproject.toml": pyproject, "uv.lock": "x\n"})
        (project.bin / "uv").write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "uv $*" >> "{project.log}"\n'
            'case "$*" in *--all-extras*) exit 2 ;; esac\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  [ "$1" = "--output-file" ] && printf "a==1\\n" > "$2"\n'
            "  shift\n"
            "done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        code, text, calls = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(0, code, text)
        exports = [c for c in calls if c.startswith("uv export")]
        selections = sorted(
            c.split("--output-file ", 1)[1].split()[0].rsplit("/", 1)[1]
            for c in exports[1:]
        )
        self.assertEqual(
            ["base.txt", "extra-cpu.txt", "extra-gpu.txt", "group-dev.txt"], selections
        )
        [audit] = _audits(calls)
        for name in selections:
            self.assertIn(f"/{name}", audit)

    def test_an_unexportable_lock_without_declared_conflicts_still_fails(
        self,
    ) -> None:
        project = self.project({"pyproject.toml": "[project]\n", "uv.lock": "x\n"})
        project.stub("uv", 2)
        code, text, _ = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(1, code)
        self.assertIn("outcome=not_executed (uv.lock could not be exported)", text)

    def test_a_vulnerable_lock_fails(self) -> None:
        project = self.project({"pyproject.toml": "[project]\n", "uv.lock": "x\n"})
        project.stub("pip-audit", 1)
        code, text, _ = project.run({"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(1, code)
        self.assertIn("outcome=executed_fail", text)


class ScopeTests(unittest.TestCase):
    """Row 23: a change event audits only when it brings in a dependency change."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Project(
            Path(self.tmp.name), {"pyproject.toml": "[project]\n", "uv.lock": "x\n"}
        )
        for tool in ("python", "pip", "pip-audit"):
            self.project.stub(tool)
        exporting_uv(self.project)

    def events(self, base: str, head: str) -> dict[str, dict[str, str]]:
        return {
            "pull_request": {"PR_BASE": base, "PR_HEAD": head},
            "merge_group": {"MERGE_GROUP_BASE": base, "MERGE_GROUP_HEAD": head},
            "push": {"PUSH_BEFORE": base, "EVENT_SHA": head},
        }

    def test_a_change_without_a_dependency_change_is_not_audited(self) -> None:
        head = self.project.commit("README.md", "b\n")
        for event, env in self.events(self.project.base, head).items():
            with self.subTest(event=event):
                code, text, calls = self.project.run({"EVENT_NAME": event, **env})
                self.assertEqual(0, code)
                self.assertEqual([], calls, "nothing installed or audited")
                self.assertIn(
                    "outcome=not_applicable (no dependency manifest or lock changed)",
                    text,
                )

    def test_a_dependency_change_is_audited(self) -> None:
        for name in ("uv.lock", "pyproject.toml", "sub/requirements-dev.txt"):
            with self.subTest(changed=name):
                (self.project.root / "sub").mkdir(exist_ok=True)
                head = self.project.commit(name, f"{name} changed\n")
                base = _git(self.project.root, "rev-parse", "HEAD~1")
                for event, env in self.events(base, head).items():
                    _, _, calls = self.project.run({"EVENT_NAME": event, **env})
                    self.assertEqual(1, len(_audits(calls)), event)

    def test_a_renamed_lock_is_a_dependency_change(self) -> None:
        # Rename detection would show only the new name; the old path counts.
        _git(self.project.root, "mv", "uv.lock", "lockfile.bak")
        _git(self.project.root, "commit", "-qm", "rename the lock")
        head = _git(self.project.root, "rev-parse", "HEAD")
        code, text, _ = self.project.run(
            {"EVENT_NAME": "push", "PUSH_BEFORE": self.project.base, "EVENT_SHA": head}
        )
        # Not skipped as unchanged: the lock is gone, so the audit fails.
        self.assertEqual(1, code)
        self.assertNotIn("outcome=not_applicable", text)
        self.assertIn("outcome=not_executed (no lock file)", text)

    def test_events_outside_row_23_always_audit(self) -> None:
        self.project.commit("README.md", "b\n")
        for event in ("schedule", "workflow_dispatch", "workflow_call", ""):
            with self.subTest(event=event):
                _, _, calls = self.project.run({"EVENT_NAME": event})
                self.assertEqual(1, len(_audits(calls)))

    def test_an_unreadable_range_audits(self) -> None:
        head = self.project.commit("README.md", "b\n")
        for base in ("", "0" * 40, "not-a-sha", "f" * 40):
            with self.subTest(base=base):
                _, _, calls = self.project.run(
                    {"EVENT_NAME": "push", "PUSH_BEFORE": base, "EVENT_SHA": head}
                )
                self.assertEqual(1, len(_audits(calls)))


class PinTests(unittest.TestCase):
    def test_the_audit_tools_are_cores_locked_versions(self) -> None:
        lock: dict[str, Any] = json.loads(LOCK.read_text(encoding="utf-8"))
        script = _script()
        self.assertIn(f'"uv=={lock["uv"]}"', script)
        self.assertIn(f'"pip-audit=={lock["pip-audit"]}"', script)


if __name__ == "__main__":
    unittest.main()
