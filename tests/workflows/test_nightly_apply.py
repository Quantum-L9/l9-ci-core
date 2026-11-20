"""Nightly apply (switch default closed) and Core's deep receipt gate.

Apply dispatches an ELIGIBLE repository's admitted deep entrypoint through
workflow_dispatch and nothing else. The four contract cases: an unchanged
repository is skipped, a changed repository is dispatched once, a repeat run
is deduped, and an unknown permission or history dispatches nothing.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import nightly_apply as apply  # noqa: E402
import nightly_dispatch as nightly  # noqa: E402

EVALUATED = datetime(2026, 11, 20, 7, 17, tzinfo=timezone.utc)
SLOT = "2026-11-20"
HEAD = "c" * 40
OLD = "d" * 40
OPEN = {apply.SWITCH_ENV: "open"}
REPO = "Quantum-L9/alpha"
WORKFLOW = ".github/workflows/deep-plan.yml"
CONTRACT = "alpha-deep/v1"
BASE = "/repos/Quantum-L9/alpha"
PROJECTION = nightly.RegistryProjection(("Quantum-L9/l9-ci-core", REPO), "e" * 40)
ENTRIES = (
    nightly.DeepEntrypoint(
        "Quantum-L9/l9-ci-core",
        ".github/workflows/nightly-deep.yml",
        "core-deep/v1",
        "self",
    ),
    nightly.DeepEntrypoint(REPO, WORKFLOW, CONTRACT, "dispatch"),
)


class FakeGitHub:
    """In-memory REST surface for one repository. Records every call."""

    def __init__(
        self,
        *,
        head: str = HEAD,
        committed: str = "2026-11-20T03:00:00Z",
        runs: list[dict[str, object]] | None = None,
        repo_status: int = 200,
        workflow_status: int = 200,
        runs_status: int = 200,
        dispatch_status: int = 204,
    ) -> None:
        self.head = head
        self.committed = committed
        self.runs = runs if runs is not None else []
        self.repo_status = repo_status
        self.workflow_status = workflow_status
        self.runs_status = runs_status
        self.dispatch_status = dispatch_status
        self.calls: list[tuple[str, str]] = []
        self.dispatched: list[Mapping[str, object]] = []

    def __call__(
        self, method: str, path: str, body: Mapping[str, object] | None
    ) -> tuple[int, object]:
        self.calls.append((method, path))
        if method == "POST":
            if path != f"{BASE}/actions/workflows/deep-plan.yml/dispatches":
                raise AssertionError(path)
            assert body is not None
            self.dispatched.append(body)
            if self.dispatch_status in {200, 204}:
                inputs = body["inputs"]
                assert isinstance(inputs, dict)
                self.runs.insert(
                    0,
                    {
                        "id": 900 + len(self.dispatched),
                        "display_title": (
                            f"deep apply {inputs['utc_slot']} "
                            f"{inputs['deep_contract']} {inputs['dispatch_key']}"
                        ),
                        "head_sha": inputs["expected_sha"],
                        "conclusion": None,
                        "status": "queued",
                    },
                )
            return self.dispatch_status, None
        if path == BASE:
            return self.repo_status, {
                "id": 41,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
                "fork": False,
            }
        if path == f"{BASE}/commits/main":
            return 200, {
                "sha": self.head,
                "commit": {"committer": {"date": self.committed}},
            }
        if path == f"{BASE}/actions/workflows/deep-plan.yml":
            return self.workflow_status, {"state": "active"}
        if path.startswith(f"{BASE}/actions/workflows/deep-plan.yml/runs?"):
            return self.runs_status, {"workflow_runs": list(self.runs)}
        raise AssertionError(path)


def success(sha: str, slot: str = "2026-11-19") -> dict[str, object]:
    return {
        "id": 7,
        "display_title": f"deep apply {slot} {CONTRACT} nd1-{'0' * 40}",
        "head_sha": sha,
        "conclusion": "success",
        "status": "completed",
    }


def run(
    github: FakeGitHub,
    env: Mapping[str, str] = OPEN,
    *,
    evaluated: datetime = EVALUATED,
) -> dict[str, object]:
    return apply.apply_plan(
        PROJECTION, ENTRIES, env=env, transport=github, evaluated_at=evaluated
    )


def only_row(result: Mapping[str, object]) -> Mapping[str, object]:
    rows = result["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


class SwitchTests(unittest.TestCase):
    def test_switch_defaults_closed_and_reads_nothing(self) -> None:
        for env in (
            {},
            {apply.SWITCH_ENV: ""},
            {apply.SWITCH_ENV: "true"},
            {apply.SWITCH_ENV: "OPEN"},
            {apply.SWITCH_ENV: "open "},
        ):
            with self.subTest(env=env):
                github = FakeGitHub()
                result = run(github, env)
                self.assertEqual("APPLY_CLOSED", result["status"])
                self.assertEqual("closed", result["switch"])
                self.assertEqual(0, result["dispatch_count"])
                self.assertEqual([], github.calls)
                self.assertEqual(1, apply.apply_exit_code(result))

    def test_open_switch_without_a_credential_fails_closed(self) -> None:
        result = apply.apply_plan(
            PROJECTION, ENTRIES, env=OPEN, transport=None, evaluated_at=EVALUATED
        )
        self.assertEqual("UNKNOWN_PERMISSION", result["status"])
        self.assertEqual(0, result["dispatch_count"])
        self.assertEqual(1, apply.apply_exit_code(result))

    def test_no_workflow_opens_the_switch_or_holds_the_credential(self) -> None:
        for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertNotIn(apply.SWITCH_ENV, text)
                self.assertNotIn(apply.DISPATCH_TOKEN_ENV, text)
                self.assertNotIn("nightly_apply.py apply", text)


class ApplyTests(unittest.TestCase):
    def test_unchanged_repository_is_skipped(self) -> None:
        github = FakeGitHub(runs=[success(HEAD)])
        row = only_row(run(github))
        self.assertEqual("SKIPPED_ALREADY_TESTED", row["verdict"])
        self.assertEqual([], github.dispatched)
        quiet = FakeGitHub(committed="2026-11-01T00:00:00Z", runs=[success(HEAD)])
        self.assertEqual("SKIPPED_INACTIVE", only_row(run(quiet))["verdict"])
        self.assertEqual([], quiet.dispatched)

    def test_changed_repository_is_dispatched_once(self) -> None:
        github = FakeGitHub(runs=[success(OLD)])
        result = run(github)
        row = only_row(result)
        self.assertEqual("DISPATCHED", row["verdict"])
        self.assertEqual(1, result["dispatch_count"])
        self.assertEqual(0, apply.apply_exit_code(result))
        self.assertEqual(1, len(github.dispatched))
        body = github.dispatched[0]
        self.assertEqual("main", body["ref"])
        inputs = body["inputs"]
        assert isinstance(inputs, dict)
        self.assertEqual(
            {
                "expected_sha": HEAD,
                "utc_slot": SLOT,
                "deep_contract": CONTRACT,
                "dispatch_key": nightly.dispatch_key(
                    repository_id=41, slot=SLOT, workflow=WORKFLOW, contract=CONTRACT
                ),
                "mode": "apply",
            },
            inputs,
        )
        # The self-scheduled Core entrypoint is never dispatched by apply.
        self.assertNotIn("l9-ci-core", " ".join(path for _, path in github.calls))

    def test_changed_outside_the_window_is_still_dispatched(self) -> None:
        github = FakeGitHub(committed="2026-11-01T00:00:00Z", runs=[success(OLD)])
        self.assertEqual("DISPATCHED", only_row(run(github))["verdict"])

    def test_repeat_run_is_deduped(self) -> None:
        github = FakeGitHub(runs=[success(OLD)])
        first = run(github)
        self.assertEqual(1, first["dispatch_count"])
        second = run(github)
        row = only_row(second)
        self.assertEqual("SKIPPED_DUPLICATE", row["verdict"])
        self.assertEqual(0, second["dispatch_count"])
        self.assertEqual(1, len(github.dispatched))
        self.assertEqual(0, apply.apply_exit_code(second))

    def test_unknown_permission_or_history_fails_closed(self) -> None:
        cases = {
            "repository_forbidden": (
                FakeGitHub(repo_status=403),
                "UNKNOWN_PERMISSION",
            ),
            "workflow_missing": (
                FakeGitHub(workflow_status=404),
                "UNKNOWN_PERMISSION",
            ),
            "repository_unreadable": (
                FakeGitHub(repo_status=500),
                "UNKNOWN_ACTIVITY",
            ),
            "history_unreadable": (
                FakeGitHub(runs_status=502),
                "UNKNOWN_RUN_HISTORY",
            ),
        }
        for name, (github, verdict) in cases.items():
            with self.subTest(name=name):
                result = run(github)
                row = only_row(result)
                self.assertEqual(verdict, row["verdict"])
                self.assertIs(True, row["alert"])
                self.assertEqual(0, result["dispatch_count"])
                self.assertEqual([], github.dispatched)
                self.assertEqual(1, apply.apply_exit_code(result))

    def test_refused_dispatch_is_an_alert(self) -> None:
        github = FakeGitHub(runs=[success(OLD)], dispatch_status=403)
        result = run(github)
        row = only_row(result)
        self.assertEqual("DISPATCH_REFUSED", row["verdict"])
        self.assertEqual(0, result["dispatch_count"])
        self.assertEqual(1, apply.apply_exit_code(result))

    def test_unprojected_entrypoint_is_not_read(self) -> None:
        github = FakeGitHub()
        result = apply.apply_plan(
            nightly.RegistryProjection(("Quantum-L9/l9-ci-core",), "e" * 40),
            ENTRIES,
            env=OPEN,
            transport=github,
            evaluated_at=EVALUATED,
        )
        self.assertEqual("SKIPPED_NOT_PROJECTED", only_row(result)["verdict"])
        self.assertEqual([], github.calls)

    def test_run_name_parser(self) -> None:
        self.assertEqual(
            ("apply", SLOT, CONTRACT, "nd1-x"),
            apply.parse_run_name(f"deep apply {SLOT} {CONTRACT} nd1-x"),
        )
        for title in (None, "CI Pipeline", "deep apply x", "ship apply a b c"):
            with self.subTest(title=title):
                self.assertIsNone(apply.parse_run_name(title))


class ReceiptGateTests(unittest.TestCase):
    SHA = "f" * 40

    def transport(self, status: int, runs: list[dict[str, object]]) -> apply.Transport:
        def call(
            method: str, path: str, body: Mapping[str, object] | None
        ) -> tuple[int, object]:
            self.assertEqual("GET", method)
            self.assertTrue(
                path.startswith(
                    "/repos/Quantum-L9/l9-ci-core/actions/workflows/"
                    "nightly-deep.yml/runs?"
                )
            )
            self.assertIn(f"head_sha={self.SHA}", path)
            self.assertIn("event=schedule", path)
            self.assertIn("status=success", path)
            return status, {"workflow_runs": runs}

        return call

    def test_head_with_a_successful_scheduled_run_skips(self) -> None:
        runs: list[dict[str, object]] = [
            {
                "id": 5,
                "head_sha": self.SHA,
                "conclusion": "success",
                "event": "schedule",
            }
        ]
        decision = apply.receipt_decision(
            self.transport(200, runs), sha=self.SHA, run_id=6
        )
        self.assertEqual((False, "successful_receipt:run:5"), decision)

    def test_anything_short_of_proof_runs_the_suite(self) -> None:
        own = [
            {
                "id": 6,
                "head_sha": self.SHA,
                "conclusion": "success",
                "event": "schedule",
            }
        ]
        other_sha = [
            {
                "id": 5,
                "head_sha": "0" * 40,
                "conclusion": "success",
                "event": "schedule",
            }
        ]
        dispatched = [
            {
                "id": 5,
                "head_sha": self.SHA,
                "conclusion": "success",
                "event": "workflow_dispatch",
            }
        ]
        cases = {
            "none": (self.transport(200, []), "no_successful_receipt"),
            "own_run": (self.transport(200, own), "no_successful_receipt"),
            "other_sha": (self.transport(200, other_sha), "no_successful_receipt"),
            "dispatch": (self.transport(200, dispatched), "no_successful_receipt"),
            "unreadable": (
                self.transport(403, []),
                "receipt_unknown:history_status:403",
            ),
            "no_token": (None, "receipt_unknown:no_token"),
        }
        for name, (transport, reason) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    (True, reason),
                    apply.receipt_decision(transport, sha=self.SHA, run_id=6),
                )
        self.assertEqual(
            (True, "receipt_unknown:malformed_sha"),
            apply.receipt_decision(self.transport(200, []), sha="HEAD", run_id=6),
        )

    def test_transport_refuses_paths_outside_its_prefix(self) -> None:
        call = apply.http_transport("t", "/repos/Quantum-L9/l9-ci-core/actions/")
        for path in (
            "/orgs/Quantum-L9/repos",
            "/repos/Quantum-L9/l9-ci-core/actions/../../other",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                call("GET", path, None)


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


class _Opener:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    def open(self, request: object, timeout: int) -> _Response:
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        assert isinstance(self.outcome, _Response)
        return self.outcome


class TransportTests(unittest.TestCase):
    PREFIX = "/repos/Quantum-L9/"

    def call(self, outcome: object) -> tuple[tuple[int, object], _Opener]:
        opener = _Opener(outcome)
        with mock.patch.object(
            apply.urllib.request, "build_opener", return_value=opener
        ):
            transport = apply.http_transport("tok", self.PREFIX)
            result = transport("POST", "/repos/Quantum-L9/a/x", {"ref": "main"})
        return result, opener

    def test_json_body_is_parsed_and_headers_are_set(self) -> None:
        result, opener = self.call(_Response(200, b'{"ok": true}'))
        self.assertEqual((200, {"ok": True}), result)
        request = opener.requests[0]
        assert isinstance(request, apply.urllib.request.Request)
        self.assertEqual(
            "https://api.github.com/repos/Quantum-L9/a/x", request.full_url
        )
        self.assertEqual("Bearer tok", request.get_header("Authorization"))
        self.assertEqual(b'{"ref": "main"}', request.data)

    def test_empty_error_and_unparseable_responses(self) -> None:
        self.assertEqual((204, None), self.call(_Response(204, b""))[0])
        self.assertEqual((0, None), self.call(_Response(200, b"not json"))[0])
        refused = urllib.error.HTTPError("u", 403, "no", {}, None)  # type: ignore[arg-type]
        self.assertEqual((403, None), self.call(refused)[0])
        self.assertEqual((0, None), self.call(urllib.error.URLError("down"))[0])
        self.assertEqual((0, None), self.call(TimeoutError())[0])

    def test_redirects_are_refused(self) -> None:
        handler = apply._NoRedirect()
        request = apply.urllib.request.Request("https://api.github.com/x")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://e.x")


class HistoryTests(unittest.TestCase):
    def test_latest_successful_apply_under_the_contract_wins(self) -> None:
        runs: list[object] = [
            {
                "display_title": f"deep apply {SLOT} {CONTRACT} k3",
                "conclusion": "failure",
                "head_sha": HEAD,
            },
            {
                "display_title": f"deep plan {SLOT} {CONTRACT} k2",
                "conclusion": "success",
                "head_sha": HEAD,
            },
            {
                "display_title": f"deep apply {SLOT} other/v1 k1",
                "conclusion": "success",
                "head_sha": HEAD,
            },
            {"display_title": "CI Pipeline", "conclusion": "success"},
            {
                "display_title": f"deep apply {SLOT} {CONTRACT} k0",
                "conclusion": "success",
                "head_sha": OLD,
            },
        ]
        self.assertEqual(
            (["k3", "k2", "k1", "k0"], OLD), apply.history_from_runs(runs, CONTRACT)
        )
        self.assertIsNone(apply.history_from_runs(["bad"], CONTRACT))

    def test_malformed_history_is_unknown(self) -> None:
        github = FakeGitHub()
        github.runs = ["not-a-run"]  # type: ignore[list-item]
        self.assertEqual("UNKNOWN_RUN_HISTORY", only_row(run(github))["verdict"])

    def test_inactive_entrypoint_is_unknown_permission(self) -> None:
        github = FakeGitHub()
        original = github.__call__

        def inactive(
            method: str, path: str, body: Mapping[str, object] | None
        ) -> tuple[int, object]:
            if path == f"{BASE}/actions/workflows/deep-plan.yml":
                return 200, {"state": "disabled_manually"}
            return original(method, path, body)

        result = apply.apply_plan(
            PROJECTION, ENTRIES, env=OPEN, transport=inactive, evaluated_at=EVALUATED
        )
        self.assertEqual("UNKNOWN_PERMISSION", only_row(result)["verdict"])


class CommandTests(unittest.TestCase):
    def run_main(self, argv: list[str], env: dict[str, str]) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out):
            code = apply.main(argv)
        return code, out.getvalue()

    def test_receipt_outside_a_schedule_runs_and_records_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            summary = Path(tmp) / "summary"
            code, printed = self.run_main(
                ["receipt", "--sha", "f" * 40, "--run-id", "1"],
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
            )
            self.assertEqual(0, code)
            self.assertIn("run=true", printed)
            self.assertEqual(
                "run=true\nreason=not_scheduled:workflow_dispatch\n",
                output.read_text(encoding="utf-8"),
            )
            self.assertIn("runs the suite", summary.read_text(encoding="utf-8"))

    def test_scheduled_receipt_without_a_token_runs(self) -> None:
        code, printed = self.run_main(
            ["receipt", "--sha", "f" * 40, "--run-id", "1"],
            {"EVENT_NAME": "schedule"},
        )
        self.assertEqual(0, code)
        self.assertIn("reason=receipt_unknown:no_token", printed)

    def test_scheduled_receipt_reads_history_with_the_token(self) -> None:
        seen: list[str] = []

        def fake(token: str, prefix: str) -> apply.Transport:
            seen.append(prefix)

            def call(
                method: str, path: str, body: Mapping[str, object] | None
            ) -> tuple[int, object]:
                return 200, {
                    "workflow_runs": [
                        {
                            "id": 4,
                            "head_sha": "f" * 40,
                            "conclusion": "success",
                            "event": "schedule",
                        }
                    ]
                }

            return call

        with mock.patch.object(apply, "http_transport", fake):
            code, printed = self.run_main(
                ["receipt", "--sha", "f" * 40, "--run-id", "9"],
                {"EVENT_NAME": "schedule", apply.RECEIPT_TOKEN_ENV: "t"},
            )
        self.assertEqual(0, code)
        self.assertIn("run=false", printed)
        self.assertEqual(["/repos/Quantum-L9/l9-ci-core/actions/"], seen)

    def test_apply_command_is_closed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main(
                ["apply", "--evaluated-at", "2026-11-20T07:17:00Z"],
                {"RUNNER_TEMP": tmp, apply.DISPATCH_TOKEN_ENV: "t"},
            )
            self.assertEqual(1, code)
            self.assertIn("status=APPLY_CLOSED switch=closed dispatch_count=0", printed)
            written = json.loads(
                (Path(tmp) / apply.APPLY_RESULT_NAME).read_text(encoding="utf-8")
            )
        self.assertEqual("APPLY_CLOSED", written["status"])

    def test_apply_command_fails_on_an_unreadable_input(self) -> None:
        with mock.patch.object(
            apply.planner,
            "load_registry_projection",
            side_effect=nightly.FixtureError("gone"),
        ):
            code, _ = self.run_main(["apply"], {})
        self.assertEqual(2, code)


class EntrypointLoaderTests(unittest.TestCase):
    def load(self, entries: object, schema: str = nightly.ENTRYPOINTS_SCHEMA) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.json"
            path.write_text(
                json.dumps({"schema": schema, "entrypoints": entries}),
                encoding="utf-8",
            )
            nightly.load_entrypoints(path)

    def test_committed_entrypoints_load(self) -> None:
        entries = nightly.load_entrypoints()
        self.assertEqual(
            ["self", "dispatch", "dispatch"], [entry.scheduling for entry in entries]
        )

    def test_malformed_entrypoints_are_refused(self) -> None:
        good = {
            "repository": REPO,
            "workflow": WORKFLOW,
            "contract": CONTRACT,
            "scheduling": "dispatch",
        }
        cases: dict[str, object] = {
            "not_list": {"a": 1},
            "not_object": ["x"],
            "repository": [dict(good, repository="alpha")],
            "workflow_dir": [dict(good, workflow="ci/deep.yml")],
            "workflow_nested": [dict(good, workflow=".github/workflows/a/b.yml")],
            "workflow_suffix": [dict(good, workflow=".github/workflows/deep.sh")],
            "contract": [dict(good, contract="bad contract")],
            "scheduling": [dict(good, scheduling="cron")],
            "self_not_core": [dict(good, scheduling="self")],
            "duplicate": [good, good],
        }
        for name, entries in cases.items():
            with self.subTest(name=name), self.assertRaises(nightly.FixtureError):
                self.load(entries)
        with self.assertRaises(nightly.FixtureError):
            self.load([good], schema="other")


class PlannerCommandTests(unittest.TestCase):
    """The plan CLI in process, so its live path is measured, not only run."""

    def plan_main(self, argv: list[str], env: dict[str, str]) -> tuple[int, str]:
        out = io.StringIO()
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            redirect_stdout(out),
            mock.patch.object(sys, "stderr", err),
        ):
            code = nightly.main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_live_plan_writes_output_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plan.json"
            summary = Path(tmp) / "summary"
            code, printed = self.plan_main(
                [
                    "plan",
                    "--mode",
                    "plan",
                    "--live",
                    "--output",
                    str(output),
                    "--evaluated-at",
                    "2026-11-20T16:07:59Z",
                ],
                {"GITHUB_STEP_SUMMARY": str(summary)},
            )
            self.assertEqual(0, code)
            self.assertIn("exit=0", printed)
            plan = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("projection", plan["registry_status"])
            self.assertIn("not a live schedule receipt", summary.read_text("utf-8"))

    def test_live_plan_without_output_prints_the_plan(self) -> None:
        code, printed = self.plan_main(["plan", "--mode", "plan", "--live"], {})
        self.assertEqual(0, code)
        self.assertIn('"schema": "l9.nightly-plan/v1"', printed)

    def test_fixture_plan_and_flag_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "f.json"
            fixture.write_text(
                json.dumps(
                    {
                        "evaluated_at": "2026-11-19T07:17:00Z",
                        "slot": "2026-11-19",
                        "registry_readable": True,
                        "pagination_complete": True,
                        "repositories": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                0,
                self.plan_main(
                    ["plan", "--mode", "plan", "--fixture", str(fixture)], {}
                )[0],
            )
            cases = {
                "neither": ["plan", "--mode", "plan"],
                "both": ["plan", "--mode", "plan", "--live", "--fixture", str(fixture)],
                "clock": [
                    "plan",
                    "--mode",
                    "plan",
                    "--fixture",
                    str(fixture),
                    "--evaluated-at",
                    "2026-11-19T07:17:00Z",
                ],
                "mode": ["plan", "--mode", "deploy", "--live"],
            }
            for name, argv in cases.items():
                with self.subTest(name=name):
                    self.assertEqual(2, self.plan_main(argv, {})[0])

    def test_projection_rejections(self) -> None:
        receipt = json.loads(nightly.REGISTRY_PROJECTION.read_text(encoding="utf-8"))
        repeated = ["Quantum-L9/a", "Quantum-L9/a"]
        digest = (
            __import__("hashlib")
            .sha256(("\n".join(repeated) + "\n").encode())
            .hexdigest()
        )
        cases: dict[str, object] = {
            "schema": dict(receipt, schema="other"),
            "commit": dict(receipt, source={"commit": "main"}),
            "members": dict(receipt, members=[]),
            "repeat": dict(receipt, members=repeated, members_sha256=digest),
            "not_object": ["x"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in cases.items():
                path = Path(tmp) / f"{name}.json"
                path.write_text(json.dumps(body), encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(nightly.FixtureError):
                    nightly.load_registry_projection(path)

    def test_projection_plan_requires_a_utc_clock(self) -> None:
        local = datetime(2026, 11, 20, 7, 17)
        with self.assertRaises(nightly.FixtureError):
            nightly.plan_from_projection(
                PROJECTION, ENTRIES, evaluated_at=local, event="schedule", mode="plan"
            )


if __name__ == "__main__":
    unittest.main()
