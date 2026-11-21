"""org-ci.yml scans a clean tree of the evaluated commit, never the work tree.

Repository verification runs the consumer's make in the checkout. A make that
rewrites a tracked file used to leave the SDK refusing its CENTRAL-DIFF
baseline ("N tracked file(s) differ from HEAD"), so every instance ended
``incomplete`` and the required check failed for a test-harness side effect,
not for the repository's code (Quantum-L9/.github PR #278). The analyze job
now checks out a clean worktree of the evaluated commit after verification
and points Semgrep, its baseline and every planned instance at it. The files
the verification changed are named as an advisory warning, never a finding.

The step's own script runs here against real git repositories.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
STEP = "scan_tree"


def _steps() -> list[dict[str, Any]]:
    document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    return document["jobs"]["analyze"]["steps"]


def _step(key: str, value: str) -> dict[str, Any]:
    [step] = [s for s in _steps() if s.get(key) == value]
    return step


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class ScanTreeScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "checkout"
        self.root.mkdir()
        git(self.root, "init", "--quiet")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        for name, text in {
            "src/app.py": "print(1)\n",
            "docs/generated.md": "v1\n",
            ".gitignore": ".l9/runtime/\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(self.root, "add", "--", "src/app.py", "docs/generated.md", ".gitignore")
        git(self.root, "commit", "--quiet", "-m", "base")
        self.revision = git(self.root, "rev-parse", "HEAD")
        self.output = Path(tmp.name) / "output"

    def run_step(self, revision: str | None = None) -> tuple[int, str, dict[str, str]]:
        self.output.write_text("", encoding="utf-8")
        completed = subprocess.run(
            ["bash", "-c", _step("id", STEP)["run"]],
            cwd=self.root,
            env={
                "PATH": os.environ["PATH"],
                "GITHUB_OUTPUT": str(self.output),
                "L9_MATRIX_ID": "m",
                "REVISION": revision or self.revision,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        pairs = (line.split("=", 1) for line in self.output.read_text().splitlines())
        return (
            completed.returncode,
            completed.stdout + completed.stderr,
            dict(pair for pair in pairs if len(pair) == 2),
        )

    def test_a_clean_checkout_gets_a_clean_scan_tree_and_no_warning(self) -> None:
        code, text, outputs = self.run_step()
        self.assertEqual(0, code, text)
        self.assertNotIn("::warning", text)
        self.assertEqual(
            (".l9/runtime/m/scan-tree", "0", ""),
            (outputs["root"], outputs["mutated-count"], outputs["mutated-files"]),
        )
        tree = self.root / outputs["root"]
        self.assertEqual(self.revision, git(tree, "rev-parse", "HEAD"))

    def test_a_mutated_checkout_is_named_and_the_scan_tree_is_still_clean(
        self,
    ) -> None:
        (self.root / "docs" / "generated.md").write_text("v2\n", encoding="utf-8")
        (self.root / "src" / "app.py").unlink()
        code, text, outputs = self.run_step()
        self.assertEqual(0, code, "advisory: the step never fails on mutation")
        self.assertIn("::warning file=docs/generated.md::repository verification", text)
        self.assertIn("::warning file=src/app.py::", text)
        self.assertEqual("2", outputs["mutated-count"])
        self.assertEqual(
            {"docs/generated.md", "src/app.py"},
            set(outputs["mutated-files"].split(",")),
        )
        tree = self.root / outputs["root"]
        self.assertEqual("", git(tree, "status", "--porcelain", "--untracked-files=no"))
        self.assertEqual("v1\n", (tree / "docs" / "generated.md").read_text())
        self.assertTrue((tree / "src" / "app.py").is_file(), "the committed code")

    def test_a_scan_tree_that_is_not_the_evaluated_revision_fails(self) -> None:
        (self.root / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
        git(self.root, "commit", "--quiet", "-am", "later")
        code, text, _ = self.run_step(revision="0" * 40)
        self.assertNotEqual(0, code, text)


class WiringTests(unittest.TestCase):
    def test_the_scan_tree_follows_verification_and_precedes_semgrep(self) -> None:
        names = [s.get("id") or s.get("name") for s in _steps()]
        self.assertLess(names.index("repository_verify"), names.index(STEP))
        self.assertLess(
            names.index(
                "Restore the checkout history for the baseline scan (CENTRAL-DIFF)"
            ),
            names.index(STEP),
        )
        self.assertLess(names.index(STEP), names.index("Run + normalize Semgrep (SDK)"))
        self.assertLess(names.index(STEP), names.index("instances"))

    def test_the_scan_tree_is_built_from_the_evaluated_revision(self) -> None:
        self.assertEqual("${{ github.sha }}", _step("id", STEP)["env"]["REVISION"])

    def test_semgrep_and_every_instance_scan_the_scan_tree(self) -> None:
        semgrep = _step("name", "Run + normalize Semgrep (SDK)")
        self.assertEqual("${{ steps.scan_tree.outputs.root }}", semgrep["with"]["root"])
        instances = _step("id", "instances")
        self.assertEqual(
            "${{ steps.scan_tree.outputs.root }}", instances["env"]["L9_SCAN_ROOT"]
        )
        self.assertNotIn('"--root", "."', instances["run"])

    def test_the_mutation_is_reported_in_the_summary_and_never_enforced(self) -> None:
        summary = _step("name", "Write central CI summary")
        self.assertEqual(
            "${{ steps.scan_tree.outputs['mutated-files'] }}",
            summary["env"]["MUTATED_FILES"],
        )
        self.assertIn("advisory", summary["run"])
        enforcing = [
            s
            for s in _steps()
            if str(s.get("name", "")).startswith("Enforce")
            and "mutated" in str(s.get("run", ""))
        ]
        self.assertEqual([], enforcing)


if __name__ == "__main__":
    unittest.main()
