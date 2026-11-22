"""CodeQL is one org-wide check, judging only what a change introduces (2026-11-21).

Operator decision (docs/codeql-check.md). The `codeql` job in org-ci.yml is
read-only (L9-CI-CORE-005): it uploads nothing on a pull request. It analyzes
the evaluated revision and the event's base with the same pinned CodeQL and
fails only on results the base does not have, at error level or security
severity high or above. These tests run the job's own scope and verdict
scripts against real repositories and SARIF documents, pin the wiring, and,
where a CodeQL executable is installed, run the whole job end to end.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"


def job() -> dict[str, Any]:
    return yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))["jobs"]["codeql"]


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


def sarif(*results: tuple[str, str, int, str], rules: dict[str, dict[str, Any]]) -> str:
    return json.dumps(
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "CodeQL",
                            "rules": [
                                {"id": key, **value} for key, value in rules.items()
                            ],
                        }
                    },
                    "results": [
                        {
                            "ruleId": rule,
                            "message": {
                                "text": f"[a user-provided value](1) reaches {rule}"
                            },
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": path},
                                        "region": {"startLine": line},
                                    }
                                }
                            ],
                            "partialFingerprints": {"primaryLocationLineHash": digest},
                        }
                        for rule, path, line, digest in results
                    ],
                }
            ],
        }
    )


RULES = {
    "py/command-line-injection": {
        "defaultConfiguration": {"level": "error"},
        "properties": {"security-severity": "9.8"},
    },
    "actions/code-injection/medium": {
        "defaultConfiguration": {"level": "warning"},
        "properties": {"security-severity": "5.0"},
    },
    "py/weak-crypto": {
        "defaultConfiguration": {"level": "warning"},
        "properties": {"security-severity": "7.5"},
    },
}


class WiringTests(unittest.TestCase):
    def test_one_read_only_check_named_codeql_beside_the_others(self) -> None:
        jobs = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))["jobs"]
        codeql = jobs["codeql"]
        self.assertEqual("CodeQL", codeql["name"])
        self.assertEqual({"contents": "read"}, codeql["permissions"])
        self.assertNotIn("needs", codeql)
        self.assertEqual(jobs["gitleaks"]["if"], codeql["if"])
        self.assertNotIn("codeql", str(jobs["analyze"].get("needs", "")))
        checkout = codeql["steps"][0]
        self.assertIs(False, checkout["with"]["persist-credentials"])
        self.assertEqual(0, checkout["with"]["fetch-depth"])

    def test_nothing_is_uploaded_and_no_codeql_action_runs(self) -> None:
        text = yaml.safe_dump(job())
        uses = [str(s.get("uses", "")) for s in job()["steps"]]
        self.assertFalse([u for u in uses if "codeql-action" in u], uses)
        self.assertNotIn("upload-sarif", text)
        self.assertNotIn("security-events", text)

    def test_the_bundle_is_version_and_checksum_pinned(self) -> None:
        script = step("codeql_install")["run"]
        self.assertRegex(script, r'CODEQL_VERSION="\d+\.\d+\.\d+"')
        self.assertRegex(script, r'CODEQL_SHA256="[0-9a-f]{64}"')
        self.assertIn('[ "${actual}" != "${CODEQL_SHA256}" ]', script)
        self.assertIn("--proto '=https'", script)

    def test_build_mode_none_and_the_security_extended_suite(self) -> None:
        script = step("codeql_analyze")["run"]
        self.assertIn("--build-mode=none", script)
        self.assertIn("codeql-suites/${language}-security-extended.qls", script)

    def test_blocking_by_default(self) -> None:
        self.assertEqual("blocking", step("codeql_verdict")["env"]["L9_CODEQL_MODE"])


class ScopeTests(unittest.TestCase):
    def scope(self, files: dict[str, str], env: dict[str, str]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            base = commit(root, {"README.md": "x\n"})
            commit(root, files)
            completed, values, _ = run_step(
                "codeql_scope",
                root,
                Path(tmp),
                {"EVENT_NAME": "pull_request", "PR_BASE": base, **env},
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            values["expected_base"] = base
            return values

    def test_actions_with_workflows_and_each_language_by_its_files(self) -> None:
        values = self.scope(
            {
                ".github/workflows/ci.yml": "on: push\n",
                "a/x.py": "",
                "web/y.tsx": "",
                "lib/z.rb": "",
                "s/M.java": "",
            },
            {},
        )
        self.assertEqual("actions python javascript ruby java", values["languages"])
        self.assertEqual(values["expected_base"], values["base"])

    def test_an_action_definition_alone_selects_actions(self) -> None:
        values = self.scope({"tools/act/action.yaml": "runs: {}\n"}, {})
        self.assertEqual("actions", values["languages"])

    def test_a_repository_without_workflows_or_code_analyzes_nothing(self) -> None:
        values = self.scope({"docs/a.md": ""}, {})
        self.assertEqual("", values["languages"])

    def test_an_event_without_a_base_has_nothing_new(self) -> None:
        values = self.scope({"a.py": ""}, {"EVENT_NAME": "workflow_dispatch"})
        self.assertEqual("", values["base"])

    def test_a_base_outside_the_clone_fails_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            commit(root, {"a.py": ""})
            completed, _, _ = run_step(
                "codeql_scope",
                root,
                Path(tmp),
                {"EVENT_NAME": "pull_request", "PR_BASE": "1" * 40},
            )
        self.assertEqual(1, completed.returncode)
        self.assertIn("::error title=CodeQL::the event's base", completed.stdout)


class VerdictTests(unittest.TestCase):
    def verdict(
        self,
        head: str,
        base: str | None,
        *,
        mode: str = "blocking",
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            results = temp / "results"
            results.mkdir()
            (results / "head-python.sarif").write_text(head, encoding="utf-8")
            if base is not None:
                (results / "base-python.sarif").write_text(base, encoding="utf-8")
            completed, _, summary = run_step(
                "codeql_verdict",
                temp,
                temp,
                {
                    "RESULTS": str(results),
                    "LANGUAGES": "python",
                    "BASE": "" if base is None else "b" * 40,
                    "L9_CODEQL_MODE": mode,
                },
            )
        return completed, summary

    OLD = ("py/command-line-injection", "app/old.py", 4, "aaaa:1")
    NEW = ("py/command-line-injection", "app/new.py", 7, "bbbb:1")
    MEDIUM = ("actions/code-injection/medium", ".github/workflows/ci.yml", 9, "cccc:1")

    def test_a_result_the_base_already_has_is_not_judged(self) -> None:
        moved = ("py/command-line-injection", "app/old.py", 40, "aaaa:1")
        completed, summary = self.verdict(
            sarif(moved, rules=RULES), sarif(self.OLD, rules=RULES)
        )
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("pass: 0 new result(s)", summary)
        self.assertNotIn("::error", completed.stdout)

    def test_a_new_high_result_fails_annotated_at_file_and_line(self) -> None:
        completed, summary = self.verdict(
            sarif(self.OLD, self.NEW, rules=RULES), sarif(self.OLD, rules=RULES)
        )
        self.assertEqual(1, completed.returncode)
        self.assertIn(
            "::error file=app/new.py,line=7,title=CodeQL py/command-line-injection::"
            "a user-provided value reaches py/command-line-injection",
            completed.stdout,
        )
        self.assertIn("findings: 1 new result(s) at error / high or above", summary)
        self.assertIn(
            "| error 9.8 | `py/command-line-injection` | `app/new.py` | 7 |", summary
        )

    def test_security_severity_high_blocks_even_at_warning_level(self) -> None:
        weak = ("py/weak-crypto", "app/c.py", 3, "dddd:1")
        completed, _ = self.verdict(sarif(weak, rules=RULES), sarif(rules=RULES))
        self.assertEqual(1, completed.returncode)

    def test_a_new_medium_result_is_reported_and_passes(self) -> None:
        completed, summary = self.verdict(
            sarif(self.MEDIUM, rules=RULES), sarif(rules=RULES)
        )
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn(
            "::warning file=.github/workflows/ci.yml,line=9,", completed.stdout
        )
        self.assertIn("pass: 1 new result(s), none at error / high or above", summary)

    def test_a_second_copy_of_a_known_result_is_new(self) -> None:
        twice = ("py/command-line-injection", "app/old.py", 9, "aaaa:2")
        same_key = ("py/command-line-injection", "app/old.py", 9, "aaaa:1")
        completed, _ = self.verdict(
            sarif(self.OLD, same_key, rules=RULES), sarif(self.OLD, rules=RULES)
        )
        self.assertEqual(
            1, completed.returncode, "a duplicate key beyond the base's count"
        )
        completed, _ = self.verdict(
            sarif(self.OLD, twice, rules=RULES), sarif(self.OLD, rules=RULES)
        )
        self.assertEqual(1, completed.returncode)

    def test_no_base_means_nothing_is_new_and_it_passes(self) -> None:
        completed, summary = self.verdict(sarif(self.NEW, rules=RULES), None)
        self.assertEqual(0, completed.returncode)
        self.assertIn("no base for this event, so nothing is new; 1 result(s)", summary)

    def test_advisory_mode_never_fails(self) -> None:
        completed, _ = self.verdict(
            sarif(self.NEW, rules=RULES), sarif(rules=RULES), mode="advisory"
        )
        self.assertEqual(0, completed.returncode)
        self.assertIn("::warning file=app/new.py,line=7,", completed.stdout)

    def test_a_missing_sarif_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            completed, _, _ = run_step(
                "codeql_verdict",
                temp,
                temp,
                {"RESULTS": str(temp), "LANGUAGES": "python", "BASE": ""},
            )
        self.assertNotEqual(0, completed.returncode)


class EndToEndTests(unittest.TestCase):
    """The scope, analyze and verdict steps with a real CodeQL.

    Skip condition: no ``codeql`` executable on PATH. The job downloads its
    pinned bundle (about 550 MB) at run time, which a unit test cannot; the
    scripts above are tested everywhere, this runs wherever CodeQL is installed.
    """

    def test_a_pull_request_adding_a_command_injection_fails(self) -> None:
        executable = shutil.which("codeql")
        if executable is None:
            self.skipTest("no codeql executable on PATH (see the docstring)")
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            root = temp / "repo"
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            base = commit(
                root,
                {
                    "app/a.py": "def ok():\n    return 1\n",
                    ".github/workflows/ci.yml": "on: push\njobs:\n  j:\n    runs-on: x\n"
                    "    steps:\n      - run: echo hi\n",
                },
            )
            commit(
                root,
                {
                    "web/new.js": "export const answer = 42;\n",
                    "app/b.py": textwrap.dedent(
                        """\
                        import os
                        from flask import Flask, request
                        app = Flask(__name__)

                        @app.route("/run")
                        def run():
                            os.system(request.args["cmd"])
                            return "ok"
                        """
                    ),
                },
            )
            _, scope, _ = run_step(
                "codeql_scope",
                root,
                temp,
                {"EVENT_NAME": "pull_request", "PR_BASE": base},
            )
            completed, analyzed, _ = run_step(
                "codeql_analyze",
                root,
                temp,
                {
                    "CODEQL": executable,
                    "LANGUAGES": scope["languages"],
                    "BASE": scope["base"],
                },
            )
            self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
            analyze_log = completed.stdout
            completed, _, summary = run_step(
                "codeql_verdict",
                root,
                temp,
                {
                    "RESULTS": analyzed["directory"],
                    "LANGUAGES": scope["languages"],
                    "BASE": scope["base"],
                    "L9_CODEQL_MODE": "blocking",
                },
            )
        self.assertEqual(1, completed.returncode, completed.stdout)
        self.assertRegex(
            completed.stdout,
            re.escape(
                "::error file=app/b.py,line=7,title=CodeQL py/command-line-injection::"
            ),
        )
        self.assertIn("findings: 1 new result(s)", summary)
        self.assertEqual("actions python javascript", scope["languages"])
        self.assertIn("CodeQL base javascript: no source on this side", analyze_log)


if __name__ == "__main__":
    unittest.main()
