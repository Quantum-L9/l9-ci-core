"""``self-toolchain-audit.yml``: Core's pinned toolchain is audited.

The workflow always reports on a pull request and merge group, audits when
the change touches the toolchain lock, an audited install or the audit itself
(a known vulnerability then blocks), and audits nightly and by dispatch
regardless. Its scope step runs here against a real git history.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "self-toolchain-audit.yml"


def workflow() -> dict[object, object]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps() -> list[dict[str, object]]:
    return workflow()["jobs"]["audit"]["steps"]  # type: ignore[index]


def step(name: str) -> dict[str, object]:
    return next(s for s in steps() if s.get("name") == name or s.get("id") == name)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class TriggerTests(unittest.TestCase):
    def test_it_always_reports_on_changes_and_runs_nightly(self) -> None:
        on = workflow()[True]
        self.assertIsNone(on["pull_request"])  # no paths filter: always reports
        self.assertIn("merge_group", on)
        self.assertIn("workflow_dispatch", on)
        self.assertEqual(1, len(on["schedule"]))
        self.assertEqual({"contents": "read"}, workflow()["permissions"])

    def test_the_audit_runs_only_when_in_scope_and_blocks_on_a_finding(self) -> None:
        for name in (
            "Install Core's pinned uv and pip-audit",
            "Resolve the pinned toolchain",
            "Audit the pinned toolchain (pip-audit)",
        ):
            self.assertEqual("steps.scope.outputs.applies == 'true'", step(name)["if"])
        run = str(step("Audit the pinned toolchain (pip-audit)")["run"])
        self.assertIn("--disable-pip --no-deps", run)
        self.assertIn('for layer in "${out}"/layer-*.txt', run)
        self.assertIn("exit 1", run)
        self.assertNotIn("continue-on-error", WORKFLOW.read_text(encoding="utf-8"))

    def test_the_tool_versions_come_from_the_lock(self) -> None:
        run = str(step("Install Core's pinned uv and pip-audit")["run"])
        self.assertIn("toolchain-lock.json", run)
        self.assertIn('lock["uv"]', run)
        self.assertIn('lock["pip-audit"]', run)
        self.assertNotRegex(run, r"uv==\d")
        resolve = str(step("Resolve the pinned toolchain")["run"])
        self.assertIn("tools/audit_core_toolchain.py --uv uv", resolve)


class ScopeTests(unittest.TestCase):
    """The scope step's own script, against a two-commit history."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "core"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        (self.root / "README.md").write_text("a\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.output = Path(self._tmp.name) / "output"

    def change(self, path: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", f"change {path}")

    def applies(self, event: str, base: str | None = None) -> str:
        self.output.write_text("", encoding="utf-8")
        env = {
            "PATH": os.environ["PATH"],
            "GITHUB_OUTPUT": str(self.output),
            "EVENT_NAME": event,
            "PR_BASE": self.base if base is None else base,
            "MERGE_GROUP_BASE": self.base if base is None else base,
        }
        subprocess.run(
            ["bash", "-c", str(step("scope")["run"])],
            cwd=self.root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        (line,) = self.output.read_text(encoding="utf-8").splitlines()
        return line

    def test_a_change_to_an_audited_file_is_audited(self) -> None:
        for path in (
            ".github/actions/install-consumer-ci/toolchain-lock.json",
            ".github/actions/install-consumer-ci/core-owned-tools.json",
            ".github/actions/install-consumer-ci/requirements-consumer-ci.txt",
            ".github/actions/install-semgrep/locks/semgrep-1.172.0.txt",
            ".github/actions/install-consumer-ci/install.sh",
            ".github/actions/install-consumer-ci/action.yml",
            ".github/actions/install-semgrep/install.sh",
            ".github/actions/install-semgrep/action.yml",
            ".github/workflows/security.yml",
            "tools/audit_core_toolchain.py",
            ".github/workflows/self-toolchain-audit.yml",
        ):
            with self.subTest(path=path):
                self.change(path)
                for event in ("pull_request", "merge_group"):
                    self.assertEqual("applies=true", self.applies(event))

    def test_any_other_change_is_not_applicable(self) -> None:
        self.change("tools/other.py")
        self.change(".github/actions/install-consumer-ci/README.md")
        self.change(".github/workflows/self-ci.yml")
        for event in ("pull_request", "merge_group"):
            self.assertEqual("applies=false", self.applies(event))

    def test_nightly_and_dispatch_always_audit(self) -> None:
        self.change("tools/other.py")
        for event in ("schedule", "workflow_dispatch"):
            self.assertEqual("applies=true", self.applies(event))

    def test_an_unreadable_range_is_audited(self) -> None:
        self.change("tools/other.py")
        for base in ("", "not-a-sha", "f" * 40):
            with self.subTest(base=base):
                self.assertEqual("applies=true", self.applies("pull_request", base))


if __name__ == "__main__":
    unittest.main()
