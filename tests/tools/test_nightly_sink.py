"""The nightly findings sink (``tools/nightly_sink.py``, NIGHTLY-SINK).

A fake transport plays GitHub: it records every call and answers from fixed
state, so publishing, the rolling issue and the ratchet are observed end to
end without a network or a credential.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import subprocess
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import nightly_sink as sink  # noqa: E402

REPO = "Quantum-L9/Example"
SHA = "a" * 40
CREATOR = "l9-nightly-dispatch[bot]"


def sarif(*rules: str) -> dict[str, Any]:
    return {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "Semgrep"}}, "results": list(rules)}],
    }


def alert(
    number: int, category: str = "l9-central/python/", rule: str = "r"
) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/security/code-scanning/{number}",
        "rule": {"id": rule, "security_severity_level": "high"},
        "most_recent_instance": {
            "category": category,
            "location": {"path": "src/a.py", "start_line": number},
        },
    }


FINGERPRINT = "f" * 64


def finding(
    line: int, rule: str = "r", fingerprint: str = FINGERPRINT
) -> dict[str, Any]:
    """A canonical bundle finding that projects onto ``alert(line, rule=rule)``."""
    return {
        "provider_id": "semgrep",
        "provider_rule_id": rule,
        "fingerprint": fingerprint,
        "locations": [{"normalized_path": "src/a.py", "start_line": line}],
    }


def waiver(
    expires: str = "2026-12-31", rule: str = "r", fingerprint: str = FINGERPRINT
) -> dict[str, Any]:
    return {
        "id": "w-1",
        "owner": "@quantum-l9/core",
        "reason": "accepted",
        "created": "2026-11-01",
        "expires": expires,
        "scope": {
            "repository": REPO,
            "provider_id": "semgrep",
            "provider_rule_id": rule,
            "path": "src/a.py",
        },
        "fingerprint": fingerprint,
        "source_sha256": "0" * 64,
    }


def ledger_file(directory: str, *entries: dict[str, Any]) -> Path:
    path = Path(directory) / "finding-waivers.yaml"
    path.write_text(
        json.dumps({"schema": sink.LEDGER_SCHEMA, "waivers": list(entries)})
    )
    return path


class FakeGitHub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.codeowners: str | None = "* @quantum-l9/core\n"
        self.processing = ["pending", "complete"]
        self.upload_status = 202

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        base = f"/repos/{REPO}"
        if method == "POST" and path == f"{base}/code-scanning/sarifs":
            return self.upload_status, {"id": "upload-1"}
        if method == "GET" and path.startswith(f"{base}/code-scanning/sarifs/"):
            return 200, {"processing_status": self.processing.pop(0), "errors": ["bad"]}
        if method == "GET" and path.startswith(f"{base}/code-scanning/alerts"):
            page = int(path.rsplit("page=", 1)[1])
            return 200, self.alerts[(page - 1) * 100 : page * 100]
        if method == "GET" and path.startswith(f"{base}/contents/"):
            if self.codeowners is None or "/.github/CODEOWNERS" not in path:
                return 404, None
            content = base64.b64encode(self.codeowners.encode()).decode()
            return 200, {"content": content}
        if method == "GET" and path.startswith(f"{base}/issues?"):
            return 200, self.issues
        if method == "POST" and path == f"{base}/issues":
            issue = {"number": 7, "body": body["body"], "title": body["title"]}
            self.issues.append(issue)
            return 201, issue
        if method == "PATCH" and path.startswith(f"{base}/issues/"):
            number = int(path.rsplit("/", 1)[1])
            for issue in self.issues:
                if issue["number"] == number:
                    issue["body"] = body["body"]
            return 200, {}
        if method == "POST" and path.endswith("/comments"):
            return 201, {}
        return 404, None

    def made(self, method: str, suffix: str) -> list[Any]:
        return [body for m, p, body in self.calls if m == method and p.endswith(suffix)]


class PublishTests(unittest.TestCase):
    def test_every_run_gets_its_instance_category(self) -> None:
        document = sarif()
        document["runs"].append({"tool": {"driver": {"name": "Semgrep"}}})
        out = sink.categorised(document, "python")
        self.assertEqual(
            ["l9-central/python/", "l9-central/python/"],
            [run["automationDetails"]["id"] for run in out["runs"]],
        )
        self.assertNotIn("automationDetails", document["runs"][0], "input untouched")

    def test_invalid_names_and_empty_sarif_are_refused(self) -> None:
        document = sarif()
        with self.assertRaises(sink.SinkError):
            sink.categorised(document, "../x")
        with self.assertRaises(sink.SinkError):
            sink.categorised({"runs": []}, "python")

    def test_upload_is_gzip_base64_and_waits_for_processing(self) -> None:
        github = FakeGitHub()
        result = sink.publish(
            github,
            REPO,
            SHA,
            "refs/heads/main",
            {"python": sarif()},
            sleep=lambda _: None,
        )
        self.assertEqual(
            [{"name": "python", "upload": "upload-1", "status": "complete"}], result
        )
        [body] = github.made("POST", "/code-scanning/sarifs")
        self.assertEqual((SHA, "refs/heads/main"), (body["commit_sha"], body["ref"]))
        sent = json.loads(gzip.decompress(base64.b64decode(body["sarif"])))
        self.assertEqual(
            "l9-central/python/", sent["runs"][0]["automationDetails"]["id"]
        )

    def test_a_refused_rejected_or_unfinished_upload_fails(self) -> None:
        for status, processing in ((403, ["complete"]), (202, ["failed"]), (202, [])):
            with self.subTest(status=status, processing=processing):
                github = FakeGitHub()
                github.upload_status = status
                github.processing = processing or ["pending"] * sink.PROCESSING_POLLS
                files = {"python": sarif()}
                with self.assertRaises(sink.SinkError):
                    sink.publish(
                        github,
                        REPO,
                        SHA,
                        "refs/heads/main",
                        files,
                        sleep=lambda _: None,
                    )

    def test_a_refused_codeql_upload_fails_only_after_the_others_publish(self) -> None:
        """Default setup refuses CodeQL analyses; Semgrep's must still land."""

        class RefusesCodeQL(FakeGitHub):
            def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any]:
                if method == "POST" and path.endswith("/code-scanning/sarifs"):
                    sent = json.loads(gzip.decompress(base64.b64decode(body["sarif"])))
                    category = sent["runs"][0]["automationDetails"]["id"]
                    if category.startswith("l9-central/codeql-"):
                        self.calls.append((method, path, body))
                        return 403, {"message": "default setup is enabled"}
                return super().__call__(method, path, body)

        github = RefusesCodeQL()
        github.processing = ["complete", "complete"]
        files = {"codeql-actions": sarif(), "python": sarif(), "gitleaks": sarif()}
        with self.assertRaises(sink.SinkError) as raised:
            sink.publish(
                github, REPO, SHA, "refs/heads/main", files, sleep=lambda _: None
            )
        self.assertIn("codeql-actions (HTTP 403)", str(raised.exception))
        self.assertIn("default setup", str(raised.exception))
        # All three were attempted; the two that are not CodeQL were accepted.
        self.assertEqual(3, len(github.made("POST", "/code-scanning/sarifs")))
        self.assertEqual(
            2, len([c for c in github.calls if "/code-scanning/sarifs/" in c[1]])
        )

    def test_any_other_refused_upload_still_fails_at_once(self) -> None:
        github = FakeGitHub()
        github.upload_status = 403
        with self.assertRaises(sink.SinkError) as raised:
            sink.publish(
                github,
                REPO,
                SHA,
                "refs/heads/main",
                {"codeql-python": sarif(), "python": sarif()},
                sleep=lambda _: None,
            )
        self.assertIn("refused the python upload", str(raised.exception))

    def test_an_empty_retirement_run_never_takes_codeqls_driver(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1, "l9-central/typescript/")]
        codeql = sarif()
        codeql["runs"][0]["tool"]["driver"]["name"] = "CodeQL"
        retire = sink.retired(
            sink.open_alerts(github, REPO),
            {"codeql-actions": codeql, "python": sarif()},
        )
        self.assertEqual(
            "Semgrep", retire["typescript"]["runs"][0]["tool"]["driver"]["name"]
        )


class RetireTests(unittest.TestCase):
    def tonight(self) -> dict[str, Any]:
        document = sarif()
        document["runs"][0]["tool"]["driver"].update(
            {
                "informationUri": "https://example.test",
                "version": "1.2.3",
                "rules": [{"id": "r"}],
            }
        )
        return {"python": document}

    def test_a_category_with_open_alerts_and_no_file_tonight_gets_an_empty_run(
        self,
    ) -> None:
        github = FakeGitHub()
        github.alerts = [
            alert(1),
            alert(2, "l9-central/typescript/"),
            alert(3, "l9-central/consumer-x/"),
        ]
        retire = sink.retired(sink.open_alerts(github, REPO), self.tonight())
        self.assertEqual(["consumer-x", "typescript"], sorted(retire))
        self.assertIsNot(retire["typescript"], retire["consumer-x"], "no shared state")
        run = retire["typescript"]["runs"][0]
        self.assertEqual([], run["results"])
        self.assertEqual(
            {
                "name": "Semgrep",
                "informationUri": "https://example.test",
                "version": "1.2.3",
                "rules": [],
            },
            run["tool"]["driver"],
            "the same tool as tonight's analyses, so the category's alerts close",
        )

    def test_nothing_to_retire_when_every_category_is_published(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1)]
        self.assertEqual(
            {}, sink.retired(sink.open_alerts(github, REPO), self.tonight())
        )


class AlertTests(unittest.TestCase):
    def test_only_l9_central_alerts_count_across_pages(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(n) for n in range(1, 151)] + [alert(500, "codeql/")]
        alerts = sink.open_alerts(github, REPO)
        self.assertEqual(150, len(alerts))
        self.assertNotIn(500, alerts)

    def test_codeowners_star_rule_and_absence(self) -> None:
        github = FakeGitHub()
        github.codeowners = "# owners\n*.py @py\n* @a @b\n/docs/ @docs\n"
        self.assertEqual(["@a", "@b"], sink.codeowners(github, REPO, "refs/heads/main"))
        github.codeowners = None
        self.assertEqual([], sink.codeowners(github, REPO, "refs/heads/main"))


class RatchetTests(unittest.TestCase):
    ALERTS = {1: {}, 2: {}, 3: {}}

    def snapshot(self, *alerts: int) -> dict[str, Any]:
        return {"schema": sink.SNAPSHOT_SCHEMA, "alerts": list(alerts)}

    def test_the_first_night_sets_the_baseline(self) -> None:
        verdict = sink.judge(None, self.ALERTS, {})
        self.assertEqual(
            ("pass", None, []),
            (verdict["ratchet"], verdict["previous_open"], verdict["new"]),
        )

    def test_new_fixed_and_open_against_the_previous_night(self) -> None:
        verdict = sink.judge(self.snapshot(2, 3, 4, 5), self.ALERTS, {})
        self.assertEqual([1], verdict["new"])
        self.assertEqual([4, 5], verdict["fixed"])
        self.assertEqual("pass", verdict["ratchet"])

    def test_a_rise_fails_unless_ledger_waived_findings_cover_it(self) -> None:
        previous = self.snapshot(1, 2)
        self.assertEqual("fail", sink.judge(previous, self.ALERTS, {})["ratchet"])
        verdict = sink.judge(previous, self.ALERTS, {3: "w-1"})
        self.assertEqual(
            ("pass", [1, 2], {3: "w-1"}),
            (verdict["ratchet"], verdict["open"], verdict["waived"]),
        )

    def test_an_advisory_category_is_listed_and_never_counted(self) -> None:
        """Biome is advisory nightly: its first night, or a rise, never fails."""

        def alert(category: str) -> dict[str, str]:
            return {
                "category": category,
                "rule": "r",
                "severity": "error",
                "path": "a.js",
                "line": "1",
                "url": "https://example.invalid/1",
            }

        alerts = {
            1: alert("l9-central/python/"),
            2: alert("l9-central/biome/"),
            3: alert("l9-central/biome/"),
        }
        advisory = sink.advisory_alerts(alerts)
        self.assertEqual({2, 3}, advisory)
        verdict = sink.judge(self.snapshot(1), alerts, {}, advisory)
        self.assertEqual(
            ("pass", [1], [2, 3], [], 1),
            (
                verdict["ratchet"],
                verdict["open"],
                verdict["advisory"],
                verdict["new"],
                len(verdict["accepted"]),
            ),
        )
        rendered = sink.render(
            "Quantum-L9/x", "a" * 40, [], alerts, verdict, "2026-11-22", ""
        )
        self.assertIn("- Advisory tools (reported, never counted): 2", rendered)
        self.assertIn("### Advisory (not counted)", rendered)


class LedgerTests(unittest.TestCase):
    TODAY = date(2026, 11, 21)

    def test_live_entries_for_this_repository_only(self) -> None:
        other = waiver()
        other["id"] = "w-2"
        other["scope"] = {**other["scope"], "repository": "Quantum-L9/Other"}
        with tempfile.TemporaryDirectory() as tmp:
            path = ledger_file(tmp, waiver(), other)
            self.assertEqual(
                ["w-1"], [e["id"] for e in sink.load_ledger(path, REPO, self.TODAY)]
            )

    def test_an_expired_entry_stops_excluding_its_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = ledger_file(tmp, waiver(expires="2026-11-20"))
            self.assertEqual([], sink.load_ledger(path, REPO, self.TODAY))
            path = ledger_file(tmp, waiver(expires="2026-11-21"))
            self.assertEqual(
                1,
                len(sink.load_ledger(path, REPO, self.TODAY)),
                "in force on its expiry day",
            )

    def test_a_malformed_or_missing_ledger_fails_closed(self) -> None:
        broken = waiver()
        del broken["source_sha256"]
        with tempfile.TemporaryDirectory() as tmp:
            for path in (
                ledger_file(tmp, broken),
                Path(tmp) / "absent.yaml",
            ):
                with self.subTest(path=path.name), self.assertRaises(sink.SinkError):
                    sink.load_ledger(path, REPO, self.TODAY)
            bad = Path(tmp) / "bad.yaml"
            bad.write_text(
                json.dumps({"schema": "l9.nightly-ratchet-waivers/v1", "waivers": []})
            )
            with self.assertRaises(sink.SinkError):
                sink.load_ledger(bad, REPO, self.TODAY)

    def test_the_committed_central_ledger_loads(self) -> None:
        path = ROOT / ".github/actions/resolve-governance/finding-waivers.yaml"
        self.assertIsInstance(sink.load_ledger(path, REPO, self.TODAY), list)

    def test_waived_findings_match_rule_path_and_fingerprint(self) -> None:
        bundles = {
            "python": {
                "findings": [
                    finding(2),
                    finding(3, rule="other"),
                    finding(4, fingerprint="e" * 64),
                ]
            }
        }
        self.assertEqual(
            {("l9-central/python/", "r", "src/a.py", "2"): "w-1"},
            sink.waived_findings(bundles, [waiver()]),
        )

    def test_the_alert_key_uses_the_canonical_rule_id_like_the_sarif(self) -> None:
        canonical = {**finding(2), "canonical_rule_id": "l9.canonical"}
        keys = sink.waived_findings({"python": {"findings": [canonical]}}, [waiver()])
        self.assertEqual(
            {("l9-central/python/", "l9.canonical", "src/a.py", "2")}, set(keys)
        )


class ReportTests(unittest.TestCase):
    def run_report(
        self,
        github: FakeGitHub,
        ledger: list[dict[str, Any]] | None = None,
        bundles: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return sink.report(
            github,
            REPO,
            SHA,
            "refs/heads/main",
            CREATOR,
            ledger or [],
            bundles or {},
            "2026-11-21",
            "https://github.com/Quantum-L9/l9-ci-core/actions/runs/1",
        )

    def test_the_first_night_opens_one_issue_with_owner_and_snapshot(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1), alert(2)]
        result = self.run_report(github)
        self.assertEqual((7, "pass"), (result["issue"], result["ratchet"]))
        [created] = github.made("POST", "/issues")
        self.assertEqual(sink.ISSUE_TITLE, created["title"])
        self.assertIn("@quantum-l9/core", created["body"])
        self.assertIn("first night sets the baseline", created["body"])
        self.assertEqual([1, 2], sink.snapshot_of(created["body"])["alerts"])
        self.assertIn(
            f"https://github.com/{REPO}/security/code-scanning/1", created["body"]
        )

    def test_the_next_night_updates_the_same_issue_in_place(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1), alert(2)]
        self.run_report(github)
        github.alerts = [alert(2)]
        result = self.run_report(github)
        self.assertEqual(1, len(github.made("POST", "/issues")), "never one per night")
        self.assertEqual(1, len(github.made("PATCH", "/issues/7")))
        self.assertEqual(
            ([], [1], "pass"), (result["new"], result["fixed"], result["ratchet"])
        )
        self.assertEqual([], github.made("POST", "/comments"))

    def test_an_unwaived_rise_is_recorded_on_the_issue_and_fails(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1)]
        self.run_report(github)
        github.alerts = [alert(1), alert(2)]
        result = self.run_report(github)
        self.assertEqual("fail", result["ratchet"])
        [comment] = github.made("POST", "/comments")
        self.assertIn("rose from 1 to 2", comment["body"])
        self.assertIn("central finding-waiver ledger", comment["body"])

    def test_a_failed_ratchet_keeps_the_last_accepted_snapshot(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1)]
        self.run_report(github)
        github.alerts = [alert(1), alert(2)]
        self.assertEqual("fail", self.run_report(github)["ratchet"])
        self.assertEqual([1], sink.snapshot_of(github.issues[0]["body"])["alerts"])
        again = self.run_report(github)
        self.assertEqual(
            ("fail", 1),
            (again["ratchet"], again["previous_open"]),
            "a re-run cannot clear a rise",
        )
        github.alerts = [alert(1)]
        self.assertEqual("pass", self.run_report(github)["ratchet"])

    def test_a_ledger_waived_finding_is_listed_and_not_counted(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1)]
        self.run_report(github)
        github.alerts = [alert(1), alert(2)]
        bundles = {"python": {"findings": [finding(2)]}}
        result = self.run_report(github, [waiver()], bundles)
        self.assertEqual(
            ("pass", [1], {2: "w-1"}),
            (result["ratchet"], result["open"], result["waived"]),
        )
        self.assertEqual([], github.made("POST", "/comments"))
        body = github.issues[0]["body"]
        self.assertIn("waiver `w-1`", body)
        self.assertEqual([1], sink.snapshot_of(body)["alerts"])

    def test_another_authors_issue_or_a_pull_request_is_not_the_rolling_issue(
        self,
    ) -> None:
        github = FakeGitHub()
        github.issues = [
            {"number": 3, "body": "unrelated"},
            {"number": 4, "body": sink.ISSUE_MARKER, "pull_request": {}},
        ]
        github.alerts = [alert(1)]
        self.assertEqual(7, self.run_report(github)["issue"])


PIP_AUDIT_REPORT = {
    "dependencies": [
        {
            "name": "Jinja2",
            "version": "3.1.4",
            "vulns": [
                {
                    "id": "GHSA-q2x7-8rv6-6q7h",
                    "fix_versions": ["3.1.5"],
                    "aliases": ["CVE-2024-56201"],
                    "description": "d",
                }
            ],
        },
        {"name": "anyio", "version": "4.6.2", "vulns": []},
        {
            "name": "urllib3",
            "version": "2.2.1",
            "vulns": [
                {
                    "id": "PYSEC-2024-1",
                    "fix_versions": [],
                    "aliases": [],
                    "description": "d",
                }
            ],
        },
    ],
    "fixes": [],
}


class DependencyAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "uv.lock").write_text("x\n")
        self.report = self.dir / "audit.json"
        self.report.write_text(json.dumps(PIP_AUDIT_REPORT))

    def audit(
        self,
        python: str = "true",
        outcome: str = "executed_fail",
        packages: str = "119",
    ) -> dict[str, Any]:
        return sink.dependency_audit(python, outcome, packages, self.report, self.dir)

    def test_an_executed_audit_counts_packages_and_known_vulnerabilities(self) -> None:
        audit = self.audit()
        self.assertEqual(
            ("executed", 119, "uv.lock"),
            (audit["status"], audit["packages"], audit["lock"]),
        )
        self.assertEqual(
            ["jinja2==3.1.4 GHSA-q2x7-8rv6-6q7h", "urllib3==2.2.1 PYSEC-2024-1"],
            sorted(audit["vulns"]),
        )

    def test_no_python_project_is_not_applicable(self) -> None:
        self.assertEqual("not_applicable", self.audit(python="false")["status"])

    def test_an_audit_that_did_not_conclude_is_incomplete(self) -> None:
        for outcome, packages in (
            ("", "119"),
            ("not_executed (no lock file)", ""),
            ("executed_pass", ""),
        ):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    "incomplete",
                    self.audit(outcome=outcome, packages=packages)["status"],
                )
        self.report.unlink()
        self.assertEqual("incomplete", self.audit()["status"], "no JSON report")
        self.report.write_text(json.dumps({"dependencies": []}))
        self.assertEqual(
            "incomplete", self.audit()["status"], "a failure with nothing reported"
        )

    def test_the_requirements_file_is_the_lock_without_uv_lock(self) -> None:
        (self.dir / "uv.lock").unlink()
        self.assertEqual("requirements.txt", self.audit()["lock"])

    def test_the_central_ledger_waives_a_known_vulnerability(self) -> None:
        audit = self.audit()
        entry = waiver()
        entry["scope"] = {
            "repository": REPO,
            "provider_id": "pip-audit",
            "provider_rule_id": "PYSEC-2024-1",
            "path": "uv.lock",
        }
        entry["fingerprint"] = sink.dependency_fingerprint(
            "urllib3", "2.2.1", "PYSEC-2024-1"
        )
        self.assertEqual(
            {"urllib3==2.2.1 PYSEC-2024-1": "w-1"},
            sink.waived_dependencies(audit, [entry]),
        )
        entry["scope"] = {**entry["scope"], "path": "requirements.txt"}
        self.assertEqual(
            {}, sink.waived_dependencies(audit, [entry]), "the path must match"
        )
        self.assertEqual(
            {},
            sink.waived_dependencies(audit, [waiver()]),
            "a Semgrep entry waives nothing here",
        )

    def test_the_first_night_is_the_baseline_and_a_rise_fails(self) -> None:
        audit = self.audit()
        first = sink.judge_dependencies(None, audit, {})
        self.assertEqual(
            ("pass", None, 2),
            (first["ratchet"], first["previous_open"], len(first["open"])),
        )
        previous = {"dependencies": ["urllib3==2.2.1 PYSEC-2024-1"]}
        rose = sink.judge_dependencies(previous, audit, {})
        self.assertEqual(
            ("fail", ["urllib3==2.2.1 PYSEC-2024-1"]),
            (rose["ratchet"], rose["accepted"]),
        )
        waived = sink.judge_dependencies(
            previous, audit, {"jinja2==3.1.4 GHSA-q2x7-8rv6-6q7h": "w-1"}
        )
        self.assertEqual("pass", waived["ratchet"])

    def test_an_incomplete_audit_fails_and_keeps_the_bar(self) -> None:
        previous = {"dependencies": ["a==1 X"]}
        verdict = sink.judge_dependencies(previous, self.audit(outcome=""), {})
        self.assertEqual(
            ("fail", ["a==1 X"]), (verdict["ratchet"], verdict["accepted"])
        )

    def test_the_issue_has_a_pip_audit_section_and_per_tool_counts(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1)]
        audit = self.audit()
        result = sink.report(
            github,
            REPO,
            SHA,
            "refs/heads/main",
            CREATOR,
            [],
            {},
            "2026-11-21",
            "",
            audit,
        )
        self.assertEqual("pass", result["ratchet"])
        body = github.issues[0]["body"]
        self.assertIn("- Semgrep, open in code scanning: **1**", body)
        self.assertIn(
            "- pip-audit, known vulnerabilities: **2** in 119 audited packages", body
        )
        self.assertIn("## pip-audit (full resolved lock)", body)
        self.assertIn("- Packages audited: 119 from `uv.lock`", body)
        self.assertIn(
            "[GHSA-q2x7-8rv6-6q7h](https://osv.dev/vulnerability/GHSA-q2x7-8rv6-6q7h)",
            body,
        )
        self.assertEqual(sorted(audit["vulns"]), sink.snapshot_of(body)["dependencies"])

    def test_a_vulnerability_rise_fails_the_night_and_is_recorded(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1)]
        only = dict(PIP_AUDIT_REPORT)
        only["dependencies"] = PIP_AUDIT_REPORT["dependencies"][2:]
        self.report.write_text(json.dumps(only))
        sink.report(
            github,
            REPO,
            SHA,
            "refs/heads/main",
            CREATOR,
            [],
            {},
            "2026-11-20",
            "",
            self.audit(),
        )
        self.report.write_text(json.dumps(PIP_AUDIT_REPORT))
        result = sink.report(
            github,
            REPO,
            SHA,
            "refs/heads/main",
            CREATOR,
            [],
            {},
            "2026-11-21",
            "",
            self.audit(),
        )
        self.assertEqual(
            ("fail", False), (result["ratchet"], result["rose"]), "Semgrep did not rise"
        )
        [comment] = github.made("POST", "/comments")
        self.assertIn(
            "known vulnerabilities (pip-audit) rose from 1 to 2", comment["body"]
        )
        self.assertEqual(
            1, len(github.made("POST", "/issues")), "still one rolling issue"
        )


class CommandLineTests(unittest.TestCase):
    def test_exit_codes(self) -> None:
        args = [
            "report",
            "--repository",
            REPO,
            "--commit",
            SHA,
            "--creator",
            CREATOR,
            "--ledger",
            "/nonexistent.yaml",
            "--bundle-dir",
            "/nonexistent",
            "--python-project",
            "false",
            "--pip-audit-outcome",
            "",
            "--pip-audit-packages",
            "",
            "--pip-audit-report",
            "/nonexistent.json",
            "--lock-dir",
            "/nonexistent",
        ]
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(2, sink.main(args), "no token: cannot conclude")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(
                2,
                sink.main(
                    [
                        "report",
                        "--repository",
                        "bad",
                        "--commit",
                        SHA,
                        "--creator",
                        CREATOR,
                        "--ledger",
                        "x",
                        "--bundle-dir",
                        "y",
                        "--python-project",
                        "false",
                        "--pip-audit-outcome",
                        "",
                        "--pip-audit-packages",
                        "",
                        "--pip-audit-report",
                        "/nonexistent.json",
                        "--lock-dir",
                        "/nonexistent",
                    ]
                ),
            )
        github = FakeGitHub()
        github.alerts = [alert(1)]
        github.issues = [
            {
                "number": 7,
                "body": sink.render(
                    REPO, SHA, [], {}, sink.judge(None, {}, {}), "2026-11-20", ""
                ),
            }
        ]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bundle_dir = Path(tmp.name, "bundles")
        bundle_dir.mkdir()
        (bundle_dir / "python.json").write_text(json.dumps({"findings": []}))
        args[args.index("--ledger") + 1] = str(ledger_file(tmp.name))
        args[args.index("--bundle-dir") + 1] = str(bundle_dir)
        with (
            mock.patch.dict("os.environ", {sink.TOKEN_ENV: "t"}),
            mock.patch.object(
                sink.nightly_apply, "http_transport", return_value=github
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                1, sink.main(args), "a rise from 0 to 1 with no waiver fails"
            )


class WorkflowTests(unittest.TestCase):
    """nightly-sink.yml is central-semgrep.yml's scan steps, then the sink."""

    scan = (ROOT / sink.SCAN).read_text(encoding="utf-8")
    org_ci = (ROOT / sink.ORG_CI).read_text(encoding="utf-8")
    committed = (ROOT / sink.WORKFLOW).read_text(encoding="utf-8")

    def test_committed_workflow_is_current(self) -> None:
        self.assertEqual(self.committed, sink.render_workflow(self.scan, self.org_ci))

    def sink_job_steps(self) -> list[dict[str, Any]]:
        import yaml  # noqa: PLC0415

        return yaml.safe_load(self.committed)["jobs"]["sink"]["steps"]

    def test_the_dependency_audit_is_the_pip_audit_checks_own_step(self) -> None:
        """Reused, not copied: the nightly runs the `pip-audit` check's script."""
        import yaml  # noqa: PLC0415

        security = yaml.safe_load(self.org_ci)["jobs"]["pip-audit"]["steps"]
        steps = self.sink_job_steps()
        for name in sink.AUDIT_STEPS:
            [original] = [s for s in security if s.get("name") == name]
            [inlined] = [s for s in steps if s.get("name") == name]
            self.assertEqual(original.get("run"), inlined.get("run"), name)
            self.assertEqual(original.get("uses"), inlined.get("uses"), name)
            self.assertEqual(original.get("if"), inlined.get("if"), name)

    def test_the_nightly_audits_the_full_lock_with_a_json_report(self) -> None:
        [audit] = [s for s in self.sink_job_steps() if s.get("id") == "pip_audit"]
        env = audit["env"]
        self.assertEqual("nightly", env["EVENT_NAME"], "not a PR/merge/push: full lock")
        self.assertEqual(sink.AUDIT_REPORT, env["PIP_AUDIT_REPORT"])
        self.assertEqual("", env["L9_CI_INSTALL_COMMAND"])
        self.assertEqual("${{ matrix.revision }}", env["EVENT_SHA"])
        self.assertNotIn("continue-on-error", audit, "fail-closed: never fail-open")
        steps = self.sink_job_steps()
        names = [s.get("name") or s.get("id") for s in steps]
        self.assertLess(
            names.index("Python dependency audit (pip-audit)"),
            names.index("Mint the sink token for this repository (GitHub App)"),
            "the audit and its counts do not wait on the token",
        )
        [summary] = [
            s for s in steps if s.get("name") == "Summarize the dependency audit"
        ]
        self.assertEqual(
            "${{ steps.pip_audit.outputs.packages }}", summary["env"]["PACKAGES"]
        )

    def test_each_sink_step_waits_on_the_one_before_it(self) -> None:
        """A failed audit still reaches the issue; a failed upload never does."""
        steps = self.sink_job_steps()
        names = [s.get("name") for s in steps]
        start = names.index("Fetch the sink tool (Core, this run's revision)")
        self.assertEqual(
            [
                "${{ !cancelled() && steps.project_sarif.outcome == 'success'"
                " && steps.biome_check.outcome != 'failure'"
                " && steps.gitleaks.outcome != 'failure'"
                " && steps.codeql_analyze.outcome != 'failure'"
                " && steps.actionlint_check.outcome != 'failure'"
                " && steps.bandit.outcome != 'failure' }}",
                "${{ !cancelled() && steps.sink_tool.outcome == 'success' }}",
                "${{ !cancelled() && steps.sink_token.outcome == 'success' }}",
                "${{ !cancelled() && steps.upload_sarif.outcome == 'success' }}",
            ],
            [s.get("if") for s in steps[start:]],
        )
        self.assertNotIn("pip_audit", " ".join(str(s.get("if")) for s in steps[start:]))

    def test_the_report_step_receives_the_audit(self) -> None:
        [step] = [
            s
            for s in self.sink_job_steps()
            if s.get("name")
            == "Update the nightly findings issue and apply the ratchet"
        ]
        self.assertEqual(
            "${{ steps.pip_audit.outputs.outcome }}", step["env"]["PIP_AUDIT_OUTCOME"]
        )
        self.assertEqual(
            "${{ steps.detect.outputs.python }}", step["env"]["PYTHON_PROJECT"]
        )
        for flag in ("--pip-audit-report", "--pip-audit-outcome", "--lock-dir"):
            self.assertIn(flag, step["run"])

    def test_a_missing_security_step_is_refused(self) -> None:
        moved = self.org_ci.replace(
            "name: Python dependency audit (pip-audit)", "name: Audit"
        )
        with self.assertRaises(sink.SinkError):
            sink.audit_steps(sink.pip_audit_workflow(moved))

    def test_a_missing_pip_audit_job_is_refused(self) -> None:
        with self.assertRaises(sink.SinkError):
            sink.pip_audit_workflow(
                self.org_ci.replace("\n  pip-audit:\n", "\n  audit:\n")
            )

    def test_scan_is_a_nightly_whole_tree_run_under_its_own_matrix_id(self) -> None:
        steps = sink.sink_steps(self.scan)
        self.assertIn("          INPUT_EVENT: nightly\n", steps)
        self.assertIn("          INPUT_MATRIX_ID: nightly-${{ matrix.slug }}\n", steps)
        self.assertNotIn("inputs.event", steps)
        self.assertNotIn("canary-", steps)

    def test_the_scans_own_enforcement_is_not_run(self) -> None:
        """The sink judges the findings (the ratchet), not the scan's gate."""
        steps = sink.sink_steps(self.scan)
        self.assertNotIn("Enforce central mode on SDK technical gate", steps)
        self.assertIn("Enforce central mode on SDK technical gate", self.scan)
        self.assertNotIn("needs.", steps)
        self.assertIn("name: Project SARIF (SDK-owned)", steps)

    def test_sink_token_is_minted_per_repository_with_least_permissions(self) -> None:
        import yaml  # noqa: PLC0415

        workflow = yaml.safe_load(self.committed)
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        job = workflow["jobs"]["sink"]
        self.assertEqual({"contents": "read"}, job["permissions"])
        mint = next(s for s in job["steps"] if s.get("id") == "sink_token")
        self.assertEqual(
            {
                "permission-security-events": "write",
                "permission-issues": "write",
                "permission-contents": "read",
            },
            {k: v for k, v in mint["with"].items() if k.startswith("permission-")},
        )
        self.assertEqual("${{ matrix.name }}", mint["with"]["repositories"])
        import re  # noqa: PLC0415

        self.assertEqual(
            {"L9_GITHUB_READ_TOKEN", "L9_NIGHTLY_APP_PRIVATE_KEY"},
            set(re.findall(r"secrets\.([A-Z0-9_]+)", self.committed)),
        )
        self.assertEqual(
            "github.repository == 'Quantum-L9/l9-ci-core'",
            workflow["jobs"]["plan"]["if"],
        )

    def test_the_sink_runs_the_semgrep_scan_on_a_clean_checkout_only(self) -> None:
        """No repository verification, Biome, scan tree or baseline: the
        pull-request gate's own steps are not the sink's (docs/semgrep-runs-nightly.md)."""
        steps = sink.sink_steps(self.scan)
        for absent in (
            "id: repository_verify",
            "id: biome_check",
            "id: scan_tree",
            "id: baseline",
            "baseline-commit",
        ):
            self.assertNotIn(absent, steps)
        for present in ("id: instances", "id: aggregate", "operation: semgrep-run"):
            self.assertIn(present, steps)

    def test_biome_runs_nightly_on_the_whole_tree_advisory(self) -> None:
        """The org-ci `Biome` check's own steps, after its checkout: a schedule
        has no base, so the scope is the whole tree; findings go to code
        scanning instead of failing the night (operator, 2026-11-21)."""
        steps = self.sink_job_steps()
        [scope] = [s for s in steps if s.get("id") == "biome_scope"]
        [run] = [s for s in steps if s.get("id") == "biome_check"]
        self.assertEqual("", scope["env"]["PR_BASE"])
        self.assertEqual("", scope["env"]["PUSH_BEFORE"])
        self.assertEqual("advisory", run["env"]["L9_BIOME_MODE"])
        self.assertEqual("Run Biome check (advisory in the nightly sink)", run["name"])
        names = [s.get("name") for s in steps]
        self.assertEqual(1, names.count("Checkout immutable event revision"))
        self.assertLess(
            names.index("Run Biome check (advisory in the nightly sink)"),
            names.index("Project Biome's SARIF for code scanning"),
        )
        self.assertLess(
            names.index("Project Biome's SARIF for code scanning"),
            names.index("Upload SARIF to code scanning"),
        )

    def test_biome_sarif_is_projected_with_repository_relative_uris(self) -> None:
        [project] = [
            s
            for s in self.sink_job_steps()
            if s.get("name") == "Project Biome's SARIF for code scanning"
        ]
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp, "work")
            (workspace / "src").mkdir(parents=True)
            runner = Path(tmp, "runner")
            runner.mkdir()
            sarif = {
                "runs": [
                    {
                        "results": [
                            {
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": str(workspace / "src" / "a.js")
                                            }
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
            (runner / "biome.sarif").write_text(json.dumps(sarif), encoding="utf-8")
            subprocess.run(
                ["bash", "-c", project["run"]],
                cwd=workspace,
                env={
                    "PATH": os.environ["PATH"],
                    "RUNNER_TEMP": str(runner),
                    "GITHUB_WORKSPACE": str(workspace),
                },
                check=True,
                capture_output=True,
            )
            out = json.loads((runner / "nightly-sarif" / "biome.sarif").read_text())
        location = out["runs"][0]["results"][0]["locations"][0]
        self.assertEqual(
            "src/a.js", location["physicalLocation"]["artifactLocation"]["uri"]
        )

    def test_gitleaks_runs_nightly_on_the_whole_tree_and_reports(self) -> None:
        """The check's own step, retargeted: whole tree, SARIF, never fails on findings."""
        import yaml  # noqa: PLC0415

        [scan] = [s for s in self.sink_job_steps() if s.get("id") == "gitleaks"]
        [original] = [
            s
            for s in yaml.safe_load(self.org_ci)["jobs"]["gitleaks"]["steps"]
            if s.get("id") == "gitleaks"
        ]
        self.assertEqual(original["run"], scan["run"], "reused verbatim")
        self.assertEqual("${{ !cancelled() }}", scan["if"])
        env = scan["env"]
        self.assertEqual("dir", env["GITLEAKS_TARGET"])
        self.assertEqual("false", env["GITLEAKS_FINDINGS_FAIL"])
        self.assertEqual(sink.GITLEAKS_SARIF, env["GITLEAKS_SARIF"])
        self.assertEqual("nightly", env["EVENT_NAME"])
        self.assertEqual("", env["PR_BASE"])
        self.assertEqual("${{ matrix.revision }}", env["EVENT_SHA"])
        self.assertNotIn(
            "gitleaks", sink.ADVISORY_CATEGORIES, "counted: it blocks on PRs"
        )
        self.assertNotIn("continue-on-error", scan)

    def test_a_changed_gitleaks_mode_line_is_refused(self) -> None:
        moved = self.org_ci.replace("GITLEAKS_TARGET: git\n", "GITLEAKS_TARGET: tree\n")
        with self.assertRaises(sink.SinkError):
            sink.gitleaks_steps(moved)
        with self.assertRaises(sink.SinkError):
            sink.gitleaks_steps(
                self.org_ci.replace("\n  gitleaks:\n", "\n  secrets:\n")
            )

    def test_codeql_runs_nightly_on_the_whole_tree_as_the_check_does(self) -> None:
        """The check's own scope, install and analyze steps, with no base."""
        import yaml  # noqa: PLC0415

        steps = self.sink_job_steps()
        original = {
            s.get("id"): s
            for s in yaml.safe_load(self.org_ci)["jobs"]["codeql"]["steps"]
        }
        for identifier in sink.CODEQL_STEPS:
            with self.subTest(step=identifier):
                [inlined] = [s for s in steps if s.get("id") == identifier]
                self.assertEqual(original[identifier]["run"], inlined["run"])
                self.assertEqual("${{ !cancelled() }}", inlined["if"])
        [scope] = [s for s in steps if s.get("id") == "codeql_scope"]
        self.assertEqual("nightly", scope["env"]["EVENT_NAME"])
        for key in ("PR_BASE", "MERGE_GROUP_BASE", "PUSH_BEFORE"):
            self.assertEqual("", scope["env"][key], "no base: the whole tree")
        self.assertFalse([s for s in steps if s.get("id") == "codeql_verdict"])
        [project] = [
            s
            for s in steps
            if s.get("name") == "Project CodeQL's SARIF for code scanning"
        ]
        self.assertIn('nightly-sarif/codeql-${language}.sarif"', project["run"])
        self.assertTrue(project["run"].count("head-${language}.sarif"))
        self.assertNotIn("codeql", sink.ADVISORY_CATEGORIES)

    def test_a_missing_codeql_job_or_step_is_refused(self) -> None:
        with self.assertRaises(sink.SinkError):
            sink.codeql_steps(self.org_ci.replace("\n  codeql:\n", "\n  scanning:\n"))
        with self.assertRaises(sink.SinkError):
            sink.codeql_steps(
                self.org_ci.replace("id: codeql_install\n", "id: install\n")
            )

    def test_actionlint_runs_nightly_on_every_workflow_and_reports(self) -> None:
        """The check's own steps: no base, every workflow, SARIF, counted."""
        import yaml  # noqa: PLC0415

        steps = self.sink_job_steps()
        original = {
            s.get("id"): s
            for s in yaml.safe_load(self.org_ci)["jobs"]["actionlint"]["steps"]
        }
        for identifier in sink.ACTIONLINT_STEPS:
            with self.subTest(step=identifier):
                [inlined] = [s for s in steps if s.get("id") == identifier]
                self.assertEqual(original[identifier]["run"], inlined["run"])
                self.assertIn("!cancelled()", inlined["if"])
        [scope] = [s for s in steps if s.get("id") == "actionlint_scope"]
        self.assertEqual("nightly", scope["env"]["EVENT_NAME"])
        self.assertEqual("", scope["env"]["PR_BASE"])
        [check] = [s for s in steps if s.get("id") == "actionlint_check"]
        self.assertEqual("false", check["env"]["ACTIONLINT_FINDINGS_FAIL"])
        self.assertEqual(sink.ACTIONLINT_SARIF, check["env"]["ACTIONLINT_SARIF"])
        self.assertNotIn("actionlint", sink.ADVISORY_CATEGORIES)

    def test_a_missing_actionlint_job_or_mode_line_is_refused(self) -> None:
        with self.assertRaises(sink.SinkError):
            sink.actionlint_steps(
                self.org_ci.replace("\n  actionlint:\n", "\n  lint:\n")
            )
        with self.assertRaises(sink.SinkError):
            sink.actionlint_steps(
                self.org_ci.replace('ACTIONLINT_SARIF: ""', "ACTIONLINT_SARIF: x")
            )

    def test_bandit_runs_nightly_only_on_the_whole_tree_advisory(self) -> None:
        steps = self.sink_job_steps()
        [scan] = [s for s in steps if s.get("id") == "bandit"]
        names = [s.get("name") for s in steps]
        self.assertEqual(
            "${{ !cancelled() && steps.detect.outputs.python == 'true' }}", scan["if"]
        )
        self.assertIn('"bandit[sarif]==1.8.6"', scan["run"])
        self.assertIn(
            "*/tests/*,*/test_*.py", scan["run"], "tests excluded at any depth"
        )
        self.assertIn('-f "${format}"', scan["run"])
        self.assertNotIn("continue-on-error", scan)
        self.assertNotIn("-l", scan["run"].split(), "every severity is reported")
        [keep] = [s for s in steps if s.get("name") == "Keep bandit's full JSON report"]
        self.assertEqual(names.index(scan["name"]) + 1, names.index(keep["name"]))
        self.assertEqual("bandit-${{ matrix.slug }}", keep["with"]["name"])
        self.assertEqual("${{ runner.temp }}/bandit.json", keep["with"]["path"])
        self.assertEqual("error", keep["with"]["if-no-files-found"])
        self.assertIn("bandit", sink.ADVISORY_CATEGORIES)
        import yaml  # noqa: PLC0415

        for job in yaml.safe_load(self.org_ci)["jobs"].values():
            self.assertNotIn("bandit", yaml.safe_dump(job.get("steps", [])).lower())

    def test_the_bandit_step_writes_json_and_sarif_and_skips_nested_tests(self) -> None:
        """Runs the step's own script with the real scanner.

        Skip condition: no ``bandit`` executable on PATH. The step installs
        its pinned bandit at run time, which a unit test cannot (no network);
        a fake scanner would test nothing. The wiring test above runs
        everywhere; this one runs wherever bandit 1.8.6 is installed.
        """
        import shutil  # noqa: PLC0415

        if shutil.which("bandit") is None:
            self.skipTest("no bandit executable on PATH (see the docstring)")
        [scan] = [s for s in self.sink_job_steps() if s.get("id") == "bandit"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("src/app.py", "pkg/tests/test_x.py", "pkg/test_y.py"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(
                    "import subprocess\nsubprocess.call('ls', shell=True)\n",
                    encoding="utf-8",
                )
            fake = root / ".bin" / "python"
            fake.parent.mkdir()
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            summary = root / ".summary"
            temp = root / ".tmp"
            temp.mkdir()
            completed = subprocess.run(
                ["bash", "-c", scan["run"]],
                cwd=root,
                env={
                    "PATH": f"{fake.parent}:{os.environ['PATH']}",
                    "RUNNER_TEMP": str(temp),
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            report = json.loads((temp / "bandit.json").read_text(encoding="utf-8"))
            files = {r["filename"] for r in report["results"]}
            self.assertEqual({"./src/app.py"}, files)
            sarif_doc = json.loads(
                (temp / "nightly-sarif" / "bandit.sarif").read_text(encoding="utf-8")
            )
            uris = {
                loc["physicalLocation"]["artifactLocation"]["uri"]
                for result in sarif_doc["runs"][0]["results"]
                for loc in result["locations"]
            }
            self.assertEqual({"src/app.py"}, uris)
            self.assertIn(
                "- bandit (nightly, advisory): ", summary.read_text(encoding="utf-8")
            )

    def test_a_missing_biome_job_is_refused(self) -> None:
        with self.assertRaises(sink.SinkError):
            sink.biome_steps(self.org_ci.replace("\n  biome:\n", "\n  lint:\n"))

    def test_a_moved_cut_point_is_refused(self) -> None:
        moved = self.scan.replace(
            "- name: Enforce central mode on SDK technical gate", "- name: Enforce it"
        )
        with self.assertRaises(sink.SinkError):
            sink.sink_steps(moved)

    def test_render_check_reports_a_stale_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github/workflows").mkdir(parents=True)
            (root / sink.SCAN).write_text(self.scan, encoding="utf-8")
            (root / sink.ORG_CI).write_text(self.org_ci, encoding="utf-8")
            (root / sink.WORKFLOW).write_text("stale\n", encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(sink, "ROOT", root), redirect_stdout(out):
                self.assertEqual(1, sink.main(["render", "--check"]))
                self.assertEqual(0, sink.main(["render"]))
                self.assertEqual(0, sink.main(["render", "--check"]))


class PlanTests(unittest.TestCase):
    def resolved(self, entry: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
        del env
        return {
            **entry,
            "revision": SHA,
            "slug": entry["repository"].lower().replace("/", "-"),
        }

    def test_every_declared_repository_is_planned(self) -> None:
        with mock.patch.object(sink._canary(), "resolve", self.resolved):
            matrix = sink.plan_matrix(ROOT, "", {})
        names = [leg["name"] for leg in matrix["include"]]
        self.assertIn("Cursor-Governance", names)
        self.assertEqual(len(names), len(sink._canary().load_declaration(ROOT)))

    def test_one_repository_narrows_the_plan(self) -> None:
        with mock.patch.object(sink._canary(), "resolve", self.resolved):
            matrix = sink.plan_matrix(ROOT, "quantum-l9/cursor-governance", {})
        self.assertEqual(
            ["Quantum-L9/Cursor-Governance"],
            [leg["repository"] for leg in matrix["include"]],
        )

    def test_an_undeclared_repository_is_refused(self) -> None:
        with self.assertRaises(sink.SinkError):
            sink.plan_matrix(ROOT, "Quantum-L9/Elsewhere", {})

    def test_plan_writes_the_matrix_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            env = {
                "GITHUB_OUTPUT": str(output),
                "L9_NIGHTLY_REPOSITORY": "Quantum-L9/Cursor-Governance",
            }
            with (
                mock.patch.dict("os.environ", env),
                mock.patch.object(sink._canary(), "resolve", self.resolved),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, sink.main(["plan"]))
            line = output.read_text(encoding="utf-8")
        self.assertTrue(line.startswith("matrix="))
        self.assertEqual(
            "Cursor-Governance", json.loads(line[7:])["include"][0]["name"]
        )

    def test_the_ratchet_reads_the_ledger_the_scan_was_given(self) -> None:
        workflow = WorkflowTests.committed
        self.assertIn(
            "L9_FINDING_WAIVERS: ${{ steps.gov.outputs['finding-waivers'] }}", workflow
        )
        self.assertIn('--ledger "${L9_FINDING_WAIVERS}"', workflow)
        self.assertFalse(
            (ROOT / ".l9/nightly-ratchet-waivers.json").exists(), "no second ledger"
        )


if __name__ == "__main__":
    unittest.main()
