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
            "location": {"path": "src/a.py"},
        },
    }


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
        with self.assertRaises(sink.SinkError):
            sink.categorised(sarif(), "../x")
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
                with self.assertRaises(sink.SinkError):
                    sink.publish(
                        github,
                        REPO,
                        SHA,
                        "refs/heads/main",
                        {"python": sarif()},
                        sleep=lambda _: None,
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
        verdict = sink.judge(None, self.ALERTS, None)
        self.assertEqual(
            ("pass", None, []),
            (verdict["ratchet"], verdict["previous_open"], verdict["new"]),
        )

    def test_new_fixed_and_open_against_the_previous_night(self) -> None:
        verdict = sink.judge(self.snapshot(2, 3, 4, 5), self.ALERTS, None)
        self.assertEqual([1], verdict["new"])
        self.assertEqual([4, 5], verdict["fixed"])
        self.assertEqual("pass", verdict["ratchet"])

    def test_a_rise_fails_unless_a_waiver_covers_it(self) -> None:
        previous = self.snapshot(1, 2)
        self.assertEqual("fail", sink.judge(previous, self.ALERTS, None)["ratchet"])
        covered = {"allow_open": 3}
        self.assertEqual("pass", sink.judge(previous, self.ALERTS, covered)["ratchet"])
        short = {"allow_open": 2}
        self.assertEqual("fail", sink.judge(previous, self.ALERTS, short)["ratchet"])

    def test_waiver_ledger_drops_expired_and_refuses_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.json"
            entry = {
                "repository": REPO,
                "allow_open": 5,
                "reason": "r",
                "approved_by": "op",
                "expires": "2026-12-31",
            }
            path.write_text(
                json.dumps({"schema": sink.WAIVER_SCHEMA, "waivers": [entry]})
            )
            self.assertIn(REPO, sink.load_waivers(path, date(2026, 11, 21)))
            self.assertEqual({}, sink.load_waivers(path, date(2027, 1, 1)))
            path.write_text(
                json.dumps(
                    {"schema": sink.WAIVER_SCHEMA, "waivers": [{"repository": REPO}]}
                )
            )
            with self.assertRaises(sink.SinkError):
                sink.load_waivers(path, date(2026, 11, 21))
            self.assertEqual(
                {}, sink.load_waivers(Path(tmp) / "absent.json", date(2026, 11, 21))
            )


class ReportTests(unittest.TestCase):
    def run_report(
        self, github: FakeGitHub, waivers: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return sink.report(
            github,
            REPO,
            SHA,
            "refs/heads/main",
            CREATOR,
            waivers or {},
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
        self.assertIn("no waiver", comment["body"])

    def test_a_waived_rise_passes_and_is_still_recorded(self) -> None:
        github = FakeGitHub()
        github.alerts = [alert(1)]
        self.run_report(github)
        github.alerts = [alert(1), alert(2)]
        waiver = {REPO: {"allow_open": 2, "reason": "upgrade", "approved_by": "op"}}
        result = self.run_report(github, waiver)
        self.assertEqual(("pass", True), (result["ratchet"], result["waived"]))
        [comment] = github.made("POST", "/comments")
        self.assertIn("waived", comment["body"])

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
            "--waivers",
            "/nonexistent.json",
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
                    ]
                ),
            )
        github = FakeGitHub()
        github.alerts = [alert(1)]
        github.issues = [
            {
                "number": 7,
                "body": sink.render(
                    REPO, SHA, [], {}, sink.judge(None, {}, None), "2026-11-20", ""
                ),
            }
        ]
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
    """nightly-sink.yml is org-ci.yml's analyze steps, then the sink."""

    org_ci = (ROOT / ".github/workflows/org-ci.yml").read_text(encoding="utf-8")
    committed = (ROOT / sink.WORKFLOW).read_text(encoding="utf-8")

    def test_committed_workflow_is_current(self) -> None:
        self.assertEqual(self.committed, sink.render_workflow(self.org_ci))

    def test_scan_is_a_nightly_whole_tree_run_under_its_own_matrix_id(self) -> None:
        steps = sink.sink_steps(self.org_ci)
        self.assertIn("          INPUT_EVENT: nightly\n", steps)
        self.assertIn("          INPUT_MATRIX_ID: nightly-${{ matrix.slug }}\n", steps)
        self.assertNotIn("inputs.event", steps)
        self.assertNotIn("canary-", steps)

    def test_pull_request_enforcement_steps_are_not_run(self) -> None:
        steps = sink.sink_steps(self.org_ci)
        self.assertNotIn("Enforce repository verification", steps)
        self.assertNotIn("needs.", steps)
        self.assertIn("name: Project SARIF (SDK-owned)", steps)

    def test_sink_token_is_minted_per_repository_with_least_permissions(self) -> None:
        import yaml  # noqa: PLC0415

        workflow = yaml.safe_load(self.committed)
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["sink"]
        self.assertEqual(job["permissions"], {"contents": "read"})
        mint = next(s for s in job["steps"] if s.get("id") == "sink_token")
        self.assertEqual(
            {k: v for k, v in mint["with"].items() if k.startswith("permission-")},
            {
                "permission-security-events": "write",
                "permission-issues": "write",
                "permission-contents": "read",
            },
        )
        self.assertEqual(mint["with"]["repositories"], "${{ matrix.name }}")
        import re  # noqa: PLC0415

        self.assertEqual(
            set(re.findall(r"secrets\.([A-Z0-9_]+)", self.committed)),
            {"L9_GITHUB_READ_TOKEN", "L9_NIGHTLY_APP_PRIVATE_KEY"},
        )
        self.assertEqual(
            workflow["jobs"]["plan"]["if"],
            "github.repository == 'Quantum-L9/l9-ci-core'",
        )

    def test_a_moved_cut_point_is_refused(self) -> None:
        moved = self.org_ci.replace(
            "- name: Enforce repository verification", "- name: Enforce it"
        )
        with self.assertRaises(sink.SinkError):
            sink.sink_steps(moved)

    def test_render_check_reports_a_stale_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/org-ci.yml").write_text(
                self.org_ci, encoding="utf-8"
            )
            (root / sink.WORKFLOW).write_text("stale\n", encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(sink, "ROOT", root), redirect_stdout(out):
                self.assertEqual(sink.main(["render", "--check"]), 1)
                self.assertEqual(sink.main(["render"]), 0)
                self.assertEqual(sink.main(["render", "--check"]), 0)


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
            [leg["repository"] for leg in matrix["include"]],
            ["Quantum-L9/Cursor-Governance"],
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
                self.assertEqual(sink.main(["plan"]), 0)
            line = output.read_text(encoding="utf-8")
        self.assertTrue(line.startswith("matrix="))
        self.assertEqual(
            json.loads(line[7:])["include"][0]["name"], "Cursor-Governance"
        )

    def test_committed_waiver_ledger_is_well_formed(self) -> None:
        self.assertEqual(sink.load_waivers(ROOT / sink.WAIVERS, date(2026, 11, 21)), {})


if __name__ == "__main__":
    unittest.main()
