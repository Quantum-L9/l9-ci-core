"""gitleaks, pip-audit and npm-audit are org-CI checks of their own (2026-11-21).

Operator decision (docs/security-checks.md): the security scan leaves the
required `analyze` job. Each tool is its own check, judges incoming code only
and always reports; bandit is nightly only (nightly-sink.yml). These tests run
each check's own scripts against real git repositories and fake reports, and
pin the wiring: no check waits on another, `analyze` needs none of them, and a
secret's value never reaches the log or the summary.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
CHECKS = ("gitleaks", "pip-audit", "npm-audit")
ZERO = "0" * 40


def workflow() -> dict[Any, Any]:
    return yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))


def step(job: str, identifier: str) -> dict[str, Any]:
    [found] = [s for s in workflow()["jobs"][job]["steps"] if s.get("id") == identifier]
    return found


def heredocs(script: str) -> list[str]:
    """The Python bodies a step runs through ``python3 -I - <<'PY'``."""
    bodies = re.findall(r"<<'PY'\n(.*?)\n\s*PY\n", script, flags=re.S)
    return [textwrap.dedent(body) for body in bodies]


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


def repository(
    root: Path, base: dict[str, str], change: dict[str, str]
) -> tuple[str, str]:
    """A repository with two commits; returns (base sha, head sha)."""
    git(root, "init", "-q", "-b", "main")
    for files in (base, change):
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "--allow-empty", "-m", "c")
    return git(root, "rev-parse", "HEAD~1"), git(root, "rev-parse", "HEAD")


def run(
    script: str, root: Path, env: dict[str, str], *, program: list[str] | None = None
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    output = root / ".out"
    summary = root / ".summary"
    output.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")
    completed = subprocess.run(
        program or ["bash", "-c", script],
        input=script if program else None,
        cwd=root,
        env={
            "PATH": os.environ["PATH"],
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_TEMP": str(root / ".tmp"),
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, output.read_text(), summary.read_text()


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = workflow()["jobs"]

    def test_each_tool_is_its_own_named_read_only_check(self) -> None:
        for name in CHECKS:
            with self.subTest(check=name):
                job = self.jobs[name]
                self.assertEqual(name, job["name"])
                self.assertEqual({"contents": "read"}, job["permissions"])
                self.assertNotIn("needs", job)
                self.assertNotIn("uses", job)
                checkout = job["steps"][0]
                self.assertIs(False, checkout["with"]["persist-credentials"])
                self.assertEqual(0, checkout["with"]["fetch-depth"])

    def test_each_check_runs_exactly_when_analyze_would(self) -> None:
        gate = " ".join(self.jobs["analyze"]["if"].split())
        prefix = "!cancelled() && ("
        self.assertTrue(gate.startswith(prefix), gate)
        for name in CHECKS:
            with self.subTest(check=name):
                self.assertEqual(
                    gate[len(prefix) : -1], " ".join(self.jobs[name]["if"].split())
                )

    def test_analyze_no_longer_waits_for_or_enforces_a_security_scan(self) -> None:
        analyze = self.jobs["analyze"]
        self.assertNotIn("needs", analyze)
        self.assertNotIn("security", self.jobs)
        names = [str(s.get("name", "")) for s in analyze["steps"]]
        self.assertFalse([n for n in names if "security" in n.lower()], names)

    def test_bandit_is_not_a_pull_request_check(self) -> None:
        self.assertNotIn("bandit", self.jobs)
        for name, job in self.jobs.items():
            for entry in job.get("steps", []):
                with self.subTest(
                    job=name, step=entry.get("name") or entry.get("uses")
                ):
                    self.assertNotIn("bandit", yaml.safe_dump(entry).lower())

    def test_gitleaks_and_pip_audit_block_and_npm_audit_stays_opt_in(self) -> None:
        self.assertEqual("event", step("gitleaks", "gitleaks")["env"]["GITLEAKS_SCOPE"])
        self.assertIn('exit "${status}"', step("gitleaks", "gitleaks")["run"])
        self.assertIn("sys.exit(1)", step("pip-audit", "pip_audit_verdict")["run"])
        self.assertEqual("false", step("npm-audit", "npm_audit")["env"]["L9_NPM_AUDIT"])


class GitleaksSignalTests(unittest.TestCase):
    """The summary step names every finding without ever printing its value."""

    SECRET = "ghp_" + "A1b2C3d4" * 5

    def summarize(
        self, status: int, findings: object
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        [_merge, body] = heredocs(step("gitleaks", "gitleaks")["run"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            report.write_text(json.dumps(findings), encoding="utf-8")
            completed, _, summary = run(
                body,
                root,
                {"STATUS": str(status), "REPORT": str(report), "SCOPE_TEXT": "a..b"},
                program=["python3", "-I", "-"],
            )
        return completed, summary

    def test_findings_are_annotated_at_file_and_line_and_never_print_the_value(
        self,
    ) -> None:
        finding = {
            "File": "src/config.py",
            "StartLine": 7,
            "RuleID": "github-pat",
            "Commit": "0123456789abcdef0123",
            "Secret": self.SECRET,
            "Match": f"token = '{self.SECRET}'",
        }
        completed, summary = self.summarize(1, [finding])
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn(
            "::error file=src/config.py,line=7,title=gitleaks github-pat::",
            completed.stdout,
        )
        self.assertIn("::error title=gitleaks::1 secret(s) in a..b", completed.stdout)
        self.assertIn(
            "| `src/config.py` | 7 | `github-pat` | `0123456789ab` |", summary
        )
        for text in (completed.stdout, completed.stderr, summary):
            self.assertNotIn(self.SECRET, text)
            self.assertNotIn("A1b2C3d4", text)

    def test_the_nightly_mode_writes_sarif_and_reports_instead_of_failing(self) -> None:
        [_merge, body] = heredocs(step("gitleaks", "gitleaks")["run"])
        finding = {
            "File": "./src/config.py",
            "StartLine": 3,
            "RuleID": "github-pat",
            "Secret": self.SECRET,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            report.write_text(json.dumps([finding]), encoding="utf-8")
            target = root / "sarif" / "gitleaks.sarif"
            completed, _, _ = run(
                body,
                root,
                {
                    "STATUS": "1",
                    "REPORT": str(report),
                    "SCOPE_TEXT": "the whole tree",
                    "GITLEAKS_SARIF": str(target),
                    "GITLEAKS_FINDINGS_FAIL": "false",
                },
                program=["python3", "-I", "-"],
            )
            sarif = target.read_text(encoding="utf-8")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertNotIn("::error title=gitleaks::", completed.stdout)
        self.assertIn("reported to code scanning", completed.stdout)
        self.assertNotIn(self.SECRET, sarif)
        [result] = json.loads(sarif)["runs"][0]["results"]
        self.assertEqual("github-pat", result["ruleId"])
        location = result["locations"][0]["physicalLocation"]
        self.assertEqual("src/config.py", location["artifactLocation"]["uri"])
        self.assertEqual(3, location["region"]["startLine"])

    def test_pull_requests_scan_commits_and_fail_on_findings(self) -> None:
        env = step("gitleaks", "gitleaks")["env"]
        self.assertEqual("git", env["GITLEAKS_TARGET"])
        self.assertEqual("true", env["GITLEAKS_FINDINGS_FAIL"])
        self.assertEqual("", env["GITLEAKS_SARIF"])
        script = step("gitleaks", "gitleaks")["run"]
        self.assertIn('/tmp/gitleaks "${GITLEAKS_TARGET}"', script)
        self.assertIn(
            '[ "${status}" -eq 1 ] && [ "${GITLEAKS_FINDINGS_FAIL}" = "false" ]', script
        )

    def test_a_clean_scan_and_an_infrastructure_failure_say_which(self) -> None:
        completed, summary = self.summarize(0, [])
        self.assertIn("pass: no secret in a..b", summary)
        self.assertNotIn("::error", completed.stdout)
        completed, summary = self.summarize(2, None)
        self.assertIn("error: gitleaks exited 2", summary)
        self.assertIn("an infrastructure failure, not a finding", completed.stdout)

    def test_only_the_incoming_commits_are_scanned_on_a_pull_request(self) -> None:
        script = step("gitleaks", "gitleaks")["run"]
        self.assertIn('GITLEAKS_LOG_OPTS="${PR_BASE}..${PR_HEAD}"', script)
        self.assertIn(
            'GITLEAKS_LOG_OPTS="${MERGE_GROUP_BASE}..${MERGE_GROUP_HEAD}"', script
        )
        self.assertIn('--log-opts="${GITLEAKS_LOG_OPTS}"', script)
        self.assertIn("--redact", script)


class PipAuditTests(unittest.TestCase):
    def test_no_python_project_is_not_applicable_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed, output, summary = run(
                step("pip-audit", "detect")["run"], Path(tmp), {}
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("python=false", output)
        self.assertIn("not applicable: no Python project", summary)

    def test_a_change_that_touches_no_manifest_is_not_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, head = repository(
                root,
                {"pyproject.toml": "[project]\nname='x'\n", "uv.lock": "v\n"},
                {"src/app.py": "print(1)\n"},
            )
            completed, output, _ = run(
                step("pip-audit", "pip_audit")["run"],
                root,
                {"EVENT_NAME": "pull_request", "PR_BASE": base, "PR_HEAD": head},
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("outcome=not_applicable", output)
        # Nothing was installed: the step ended before pip ran.
        self.assertNotIn("pip-audit==", completed.stdout + completed.stderr)

    def test_a_manifest_change_is_audited(self) -> None:
        """The step reaches the install, which a fake python records and fails."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, head = repository(
                root,
                {"pyproject.toml": "[project]\nname='x'\n"},
                {"uv.lock": "changed\n"},
            )
            bin_dir = root / ".bin"
            bin_dir.mkdir()
            fake = bin_dir / "python"
            fake.write_text(
                "#!/bin/sh\necho reached-install\nexit 9\n", encoding="utf-8"
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            completed, output, _ = run(
                step("pip-audit", "pip_audit")["run"],
                root,
                {
                    "EVENT_NAME": "pull_request",
                    "PR_BASE": base,
                    "PR_HEAD": head,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                },
            )
        self.assertEqual(9, completed.returncode)
        self.assertIn("reached-install", completed.stdout)
        self.assertNotIn("not_applicable", output)

    def verdict(
        self, outcome: str, report: object | None = None
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        [body] = heredocs(step("pip-audit", "pip_audit_verdict")["run"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "uv.lock").write_text("", encoding="utf-8")
            path = root / "pip-audit.json"
            if report is not None:
                path.write_text(json.dumps(report), encoding="utf-8")
            completed, _, summary = run(
                body,
                root,
                {"OUTCOME": outcome, "REPORT": str(path)},
                program=["python3", "-I", "-"],
            )
        return completed, summary

    def test_unchanged_dependencies_and_a_clean_audit_pass(self) -> None:
        completed, summary = self.verdict(
            "not_applicable (no dependency manifest or lock changed)"
        )
        self.assertEqual(0, completed.returncode)
        self.assertIn("pass: dependencies unchanged", summary)
        completed, summary = self.verdict("executed_pass")
        self.assertEqual(0, completed.returncode)
        self.assertIn("pass: no known vulnerability", summary)

    def test_a_known_vulnerability_fails_named_at_the_lock(self) -> None:
        report = {
            "dependencies": [
                {
                    "name": "jinja2",
                    "version": "2.10",
                    "vulns": [{"id": "PYSEC-2019-217"}],
                },
                {"name": "ok", "version": "1", "vulns": []},
            ]
        }
        completed, summary = self.verdict("executed_fail", report)
        self.assertEqual(1, completed.returncode)
        self.assertIn(
            "::error file=uv.lock,title=pip-audit PYSEC-2019-217::jinja2 2.10",
            completed.stdout,
        )
        self.assertIn("| `jinja2` | 2.10 | PYSEC-2019-217 |", summary)

    def test_an_unreadable_report_fails(self) -> None:
        completed, _ = self.verdict("executed_fail")
        self.assertEqual(1, completed.returncode)
        self.assertIn("report is unreadable", completed.stderr)


class NpmAuditTests(unittest.TestCase):
    def audit(
        self,
        base: dict[str, str],
        change: dict[str, str],
        *,
        enabled: bool,
        npm_exit: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, head = repository(root, base, change)
            bin_dir = root / ".bin"
            bin_dir.mkdir()
            fake = bin_dir / "npm"
            fake.write_text(
                f'#!/bin/sh\necho npm "$@"\nexit {npm_exit}\n', encoding="utf-8"
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            completed, _, summary = run(
                step("npm-audit", "npm_audit")["run"],
                root,
                {
                    "L9_NPM_AUDIT": "true" if enabled else "false",
                    "EVENT_NAME": "pull_request",
                    "PR_BASE": first,
                    "EVENT_SHA": head,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                },
            )
        return completed, summary

    NODE = {"package.json": "{}\n", "package-lock.json": "{}\n"}

    def test_not_enabled_is_not_applicable(self) -> None:
        completed, summary = self.audit(
            self.NODE, {"package.json": '{"a":1}\n'}, enabled=False
        )
        self.assertEqual(0, completed.returncode)
        self.assertIn("not applicable: npm audit is opt-in and not enabled", summary)

    def test_no_node_project_is_not_applicable(self) -> None:
        completed, summary = self.audit(
            {"a.txt": "a\n"}, {"b.txt": "b\n"}, enabled=True
        )
        self.assertEqual(0, completed.returncode)
        self.assertIn("not applicable: no Node project", summary)

    def test_unchanged_dependencies_pass_without_auditing(self) -> None:
        completed, summary = self.audit(self.NODE, {"src/a.js": "1\n"}, enabled=True)
        self.assertEqual(0, completed.returncode)
        self.assertIn("pass: dependencies unchanged", summary)
        self.assertNotIn("npm audit", completed.stdout)

    def test_a_changed_lock_is_audited_at_high_and_a_finding_fails(self) -> None:
        completed, summary = self.audit(
            self.NODE, {"package-lock.json": '{"x":1}\n'}, enabled=True, npm_exit=1
        )
        self.assertEqual(1, completed.returncode)
        self.assertIn("npm audit --audit-level=high", completed.stdout)
        self.assertIn(
            "::error file=package-lock.json,title=npm-audit::", completed.stdout
        )
        self.assertIn("findings: npm audit exited 1", summary)

    def test_a_changed_manifest_without_a_lock_fails_named(self) -> None:
        completed, _ = self.audit(
            {"package.json": "{}\n"}, {"package.json": '{"a":1}\n'}, enabled=True
        )
        self.assertEqual(1, completed.returncode)
        self.assertIn("::error file=package.json,title=npm-audit::", completed.stdout)


if __name__ == "__main__":
    unittest.main()
