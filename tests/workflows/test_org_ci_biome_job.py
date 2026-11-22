"""org-ci.yml's `Biome` job: its own check, incoming code only (operator, 2026-11-21).

The job's own scope and run scripts are executed here against real git
repositories, with a fake ``biome`` that reports on exactly the paths it is
given, so each case proves which files the check judges:

* a pull request touching one file is judged on that file only, never on the
  code already there;
* a repository without biome.json / biome.jsonc succeeds, saying "not
  applicable: no biome configuration";
* a pull request that changes no file in Biome's scope succeeds, saying so;
* a finding fails the job, annotated at its file and line.

docs/biome-check.md records the decision and Biome 2.5.8's CLI it relies on.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"

# A Biome stand-in: judges the paths it is given (or the tree for "."), with
# the reporters the job asks for. A file whose name contains "bad" carries one
# error; only .js/.ts/.json files are Biome's.
FAKE_BIOME = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["FAKE_ARGV"]).write_text(json.dumps(args))
reporters, files, paths, after = [], {}, [], False
for arg in args[1:]:
    if after:
        paths.append(arg)
    elif arg == "--":
        after = True
    elif arg.startswith("--reporter="):
        reporters.append(arg.split("=", 1)[1])
    elif arg.startswith("--reporter-file="):
        files[reporters[-1]] = arg.split("=", 1)[1]
    elif not arg.startswith("-"):
        paths.append(arg)
if paths == ["."]:
    paths = [str(p) for p in Path(".").rglob("*") if p.is_file() and ".git" not in p.parts]
handled = [p for p in paths if p.endswith((".js", ".ts", ".json")) and p != "biome.json"]
bad = [p for p in handled if "bad" in Path(p).name]
diagnostics = []
for p in bad:
    print(f"::error title=lint/suspicious/noDoubleEquals,file={p},line=1,endLine=1::Use === instead of ==")
    diagnostics.append({"severity": "error", "message": "Use === instead of ==",
                        "category": "lint/suspicious/noDoubleEquals",
                        "location": {"path": p, "start": {"line": 1, "column": 1}}})
summary = {"changed": 0, "unchanged": len(handled), "errors": len(bad), "warnings": 0}
if "json" in files:
    Path(files["json"]).write_text(json.dumps({"summary": summary, "diagnostics": diagnostics}))
if "sarif" in files:
    Path(files["sarif"]).write_text(json.dumps({"runs": [{"results": []}]}))
sys.exit(int(os.environ.get("FAKE_EXIT", "1" if bad else "0")))
"""


def job() -> dict[str, Any]:
    return yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))["jobs"]["biome"]


def step(identifier: str) -> dict[str, Any]:
    [found] = [s for s in job()["steps"] if s.get("id") == identifier]
    return found


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class BiomeJobTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "checkout"
        self.root.mkdir()
        git(self.root, "init", "--quiet", "-b", "main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        self.runner = self.tmp / "runner"
        self.runner.mkdir()
        self.biome = self.tmp / "biome"
        self.biome.write_text(FAKE_BIOME, encoding="utf-8")
        self.biome.chmod(self.biome.stat().st_mode | stat.S_IXUSR)
        self.argv = self.tmp / "argv.json"

    def commit(self, files: dict[str, str], message: str) -> str:
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(self.root, "add", "--", *files)
        git(self.root, "commit", "--quiet", "-m", message)
        return git(self.root, "rev-parse", "HEAD")

    def run_job(
        self, event: str, revision: str, base: str = "", **extra: str
    ) -> tuple[int, str, str]:
        output = self.runner / "output"
        summary = self.runner / "summary"
        output.write_text("", encoding="utf-8")
        summary.write_text("", encoding="utf-8")
        env = {
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": str(self.runner),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "REPOSITORY": "Quantum-L9/fixture",
            "REVISION": revision,
            "TOKEN": "x",
            "EVENT_NAME": event,
            "PR_BASE": base if event == "pull_request" else "",
            "MERGE_GROUP_BASE": base if event == "merge_group" else "",
            "PUSH_BEFORE": base if event == "push" else "",
            "FAKE_ARGV": str(self.argv),
            **extra,
        }
        scope = subprocess.run(
            ["bash", "-c", step("biome_scope")["run"]],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        text = scope.stdout + scope.stderr
        if scope.returncode != 0:
            return scope.returncode, text, summary.read_text()
        outputs = dict(
            line.split("=", 1)
            for line in output.read_text().splitlines()
            if "=" in line
        )
        if outputs.get("applies") != "true":
            return 0, text, summary.read_text()
        run = subprocess.run(
            ["bash", "-c", step("biome_check")["run"]],
            cwd=self.root,
            env={
                **env,
                "L9_BIOME_EXECUTABLE": str(self.biome),
                "L9_BIOME_VERSION": "2.5.8",
                "L9_BIOME_SCOPE": outputs["scope"],
                "L9_BIOME_PATHS": outputs["paths"],
            },
            capture_output=True,
            text=True,
            check=False,
        )
        return run.returncode, text + run.stdout + run.stderr, summary.read_text()

    def judged(self) -> list[str]:
        args = json.loads(self.argv.read_text())
        return args[args.index("--") + 1 :] if "--" in args else args[-1:]

    def test_a_pull_request_is_judged_on_the_files_it_changes_only(self) -> None:
        base = self.commit(
            {"biome.json": "{}", "src/old_bad.js": "a == b\n"}, "existing debt"
        )
        head = self.commit({"src/new.js": "export const a = 1;\n"}, "the change")
        code, text, summary = self.run_job("pull_request", head, base)
        self.assertEqual(0, code, text)
        self.assertEqual(["src/new.js"], self.judged())
        self.assertIn("pass: 1 file(s) of the files this change touches", summary)

    def test_a_finding_fails_with_its_file_and_line_annotated(self) -> None:
        base = self.commit({"biome.json": "{}", "a.js": "ok\n"}, "base")
        head = self.commit({"src/new_bad.js": "a == b\n"}, "bad change")
        code, text, summary = self.run_job("pull_request", head, base)
        self.assertEqual(1, code)
        self.assertIn(
            "::error title=lint/suspicious/noDoubleEquals,file=src/new_bad.js,line=1",
            text,
        )
        self.assertIn("::error title=Biome::1 error(s)", text)
        self.assertIn(
            "| `src/new_bad.js` | 1 | `lint/suspicious/noDoubleEquals` |", summary
        )

    def test_no_configuration_is_a_success_that_says_so(self) -> None:
        base = self.commit({"a.js": "a == b\n"}, "base")
        head = self.commit({"bad.js": "a == b\n"}, "change")
        code, text, summary = self.run_job("pull_request", head, base)
        self.assertEqual(0, code, text)
        self.assertIn("not applicable: no biome configuration", summary)
        self.assertFalse(self.argv.exists(), "Biome never ran")

    def test_no_change_in_biomes_scope_is_a_success_that_says_so(self) -> None:
        base = self.commit({"biome.json": "{}", "bad.js": "a == b\n"}, "base")
        head = self.commit({"tool.py": "x = 1\n"}, "python only")
        code, text, summary = self.run_job("pull_request", head, base)
        self.assertEqual(0, code, text)
        self.assertIn("no changed files in Biome's scope", summary)
        self.assertEqual(["tool.py"], self.judged())

    def test_a_change_that_only_deletes_files_judges_nothing(self) -> None:
        base = self.commit({"biome.json": "{}", "gone_bad.js": "a == b\n"}, "base")
        git(self.root, "rm", "--quiet", "--", "gone_bad.js")
        git(self.root, "commit", "--quiet", "-m", "delete")
        head = git(self.root, "rev-parse", "HEAD")
        code, text, summary = self.run_job("pull_request", head, base)
        self.assertEqual(0, code, text)
        self.assertIn("no changed files in Biome's scope", summary)
        self.assertFalse(self.argv.exists())

    def test_a_merge_group_is_judged_against_its_base(self) -> None:
        base = self.commit({"biome.json": "{}", "old_bad.js": "a == b\n"}, "base")
        self.commit({"one.js": "1\n"}, "first queued")
        head = self.commit({"two.js": "2\n"}, "second queued")
        code, text, _ = self.run_job("merge_group", head, base)
        self.assertEqual(0, code, text)
        self.assertEqual(["one.js", "two.js"], sorted(self.judged()))

    def test_other_events_and_a_first_push_judge_the_whole_tree(self) -> None:
        head = self.commit({"biome.json": "{}", "debt_bad.js": "a == b\n"}, "base")
        for event, base in (
            ("workflow_dispatch", ""),
            ("push", "0" * 40),
            ("push", ""),
        ):
            with self.subTest(event=event, base=base):
                code, _, summary = self.run_job(event, head, base)
                self.assertEqual(1, code)
                self.assertIn("of the whole tree", summary)

    def test_an_unusable_base_fails_closed_and_names_it(self) -> None:
        head = self.commit({"biome.json": "{}"}, "base")
        code, text, _ = self.run_job("pull_request", head, "main")
        self.assertEqual(1, code)
        self.assertIn("::error title=Biome scope::", text)

    def test_an_infrastructure_failure_is_never_a_pass(self) -> None:
        head = self.commit({"biome.json": "{}", "a.js": "1\n"}, "base")
        code, text, _ = self.run_job("workflow_dispatch", head, FAKE_EXIT="3")
        self.assertEqual(3, code)
        self.assertIn("an infrastructure failure", text)


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        self.jobs = self.workflow["jobs"]

    def test_biome_is_its_own_check_beside_security_and_analyze(self) -> None:
        self.assertEqual("Biome", self.jobs["biome"]["name"])
        self.assertNotIn("needs", self.jobs["biome"])
        self.assertEqual(self.jobs["security"]["if"], self.jobs["biome"]["if"])
        self.assertEqual({"contents": "read"}, self.jobs["biome"]["permissions"])
        analyze = str(self.jobs["analyze"])
        self.assertNotIn("biome", self.jobs["analyze"].get("needs", ""))
        for gone in ("install-biome@", "run-biome-check@", "BIOME_STATUS"):
            self.assertNotIn(gone, analyze)

    def test_biome_is_installed_only_when_it_has_something_to_judge(self) -> None:
        install = step("biome")
        self.assertTrue(
            str(install["uses"]).startswith(
                "Quantum-L9/l9-ci-core/.github/actions/install-biome@"
            )
        )
        self.assertIn("steps.biome_scope.outputs.applies == 'true'", install["if"])
        self.assertIn("steps.biome_scope.outputs.changed != '0'", install["if"])

    def test_the_check_always_reports(self) -> None:
        """A required check is never skipped: nothing but the ref gate skips it."""
        for item in job()["steps"]:
            self.assertNotIn("continue-on-error", item)
        self.assertIn('"--reporter=github"', step("biome_check")["run"])


if __name__ == "__main__":
    unittest.main()
