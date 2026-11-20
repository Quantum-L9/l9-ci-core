"""Plan-only nightly scheduler and the pull-request full-suite refusal.

Fixtures and plan-mode artifacts are not live schedule receipts. Plan mode
dispatches zero jobs. Unknown activity, unknown permission, unknown run
history, a duplicate key, and a stale SHA do not start a heavy job.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import nightly_dispatch as nightly  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
ORG_CI = WORKFLOWS / "org-ci.yml"
NIGHTLY = WORKFLOWS / "nightly-deep.yml"
SCHEDULER = WORKFLOWS / "org-nightly-dispatch.yml"
REPO_WORKFLOW = ROOT / ".l9" / "repo-workflow.json"
SHA_A = "a" * 40
SHA_B = "b" * 40
CONTRACT = "sha256:" + "ab" * 32
EVALUATED = "2026-11-19T07:17:00+00:00"
HEAVY_IF = (
    "github.event_name == 'schedule' "
    "|| (github.event_name == 'workflow_dispatch' "
    "&& inputs.mode == 'apply' "
    "&& inputs.expected_sha == github.sha)"
)


def extract_python(run: str) -> str:
    marker = "<<'PY'\n"
    start = run.index(marker) + len(marker)
    end = run.index("\nPY", start)
    return run[start:end] + "\n"


def load(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssertionError(f"{path} did not load as a mapping")
    return data


def triggers_of(workflow: dict[str, object]) -> dict[str, object]:
    triggers = workflow.get(True, workflow.get("on"))
    if not isinstance(triggers, dict):
        raise AssertionError("workflow has no trigger mapping")
    return triggers


def repo(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "owner": "Quantum-L9",
        "name": "alpha",
        "repository_id": 1,
        "archived": False,
        "disabled": False,
        "fork": False,
        "lifecycle": "current",
        "pushed_at": "2026-11-19T07:00:00+00:00",
        "activity": "code_push",
        "activity_reason": "PushEvent refs/heads/main",
        "default_branch": "main",
        "default_sha": SHA_A,
        "deep_workflow": ".github/workflows/nightly-deep.yml",
        "deep_contract": CONTRACT,
        "run_history": "known",
        "successful_sha": None,
        "successful_contract": None,
        "existing_dispatch_keys": [],
        "permission": "known_allowed",
    }
    base.update(overrides)
    return base


def fixture(
    repositories: list[dict[str, object]],
    **overrides: object,
) -> dict[str, object]:
    body: dict[str, object] = {
        "evaluated_at": EVALUATED,
        "slot": "2026-11-19",
        "registry_readable": True,
        "pagination_complete": True,
        "repositories": repositories,
    }
    body.update(overrides)
    return body


def plan_for(
    repositories: list[dict[str, object]] | None = None,
    *,
    mode: str = "plan",
    event: str = "schedule",
    **overrides: object,
) -> dict[str, object]:
    return nightly.build_plan(
        fixture(repositories if repositories is not None else [repo()], **overrides),
        mode=mode,
        event=event,
    )


def assert_closed(test: unittest.TestCase, plan: dict[str, object]) -> None:
    test.assertEqual(0, plan["dispatch_count"])
    test.assertEqual(0, plan["heavy_job_count"])
    test.assertEqual(0, plan["consumer_checkouts"])
    test.assertEqual([], plan["selected_commands"])
    test.assertIs(False, plan["live_schedule_receipt"])
    test.assertIs(False, plan["canonical_policy"])
    test.assertIn("not a live schedule receipt", plan["receipt_note"])
    rows = plan["rows"]
    test.assertIsInstance(rows, list)
    assert isinstance(rows, list)
    for row in rows:
        test.assertIs(False, row["dispatch"])
        test.assertIs(False, row["heavy_job"])


class PlanTests(unittest.TestCase):
    def test_plan_mode_on_sixty_repos_dispatches_zero_jobs(self) -> None:
        repos = []
        for index in range(60):
            active = index < 5
            repos.append(
                repo(
                    name=f"repo-{index:02d}",
                    repository_id=1000 + index,
                    default_sha=f"{index:040x}",
                    pushed_at=(
                        "2026-11-19T07:00:00+00:00"
                        if active
                        else "2026-11-18T06:00:00+00:00"
                    ),
                )
            )
        plan = plan_for(repos)
        assert_closed(self, plan)
        self.assertEqual(5, plan["eligible_count"])
        self.assertEqual({"ELIGIBLE": 5, "SKIPPED_INACTIVE": 55}, plan["counts"])
        self.assertEqual(0, nightly.plan_exit_code(plan))
        self.assertNotIn("DISPATCH_ACCEPTED", plan["counts"])

    def test_activity_boundary_and_pushed_at_is_not_eligibility(self) -> None:
        too_old = plan_for([repo(pushed_at="2026-11-18T07:16:59+00:00")])
        self.assertEqual("SKIPPED_INACTIVE", too_old["rows"][0]["verdict"])
        exact = plan_for([repo(pushed_at="2026-11-18T07:17:00+00:00")])
        self.assertEqual("ELIGIBLE", exact["rows"][0]["verdict"])
        inside = plan_for([repo(pushed_at="2026-11-18T07:17:01+00:00")])
        self.assertEqual("ELIGIBLE", inside["rows"][0]["verdict"])
        tag = plan_for(
            [
                repo(
                    activity="negative",
                    activity_reason="tag_only",
                )
            ]
        )
        self.assertEqual("SKIPPED_INACTIVE", tag["rows"][0]["verdict"])
        issue = plan_for([repo(activity="negative", activity_reason="issue_edit")])
        self.assertEqual("SKIPPED_INACTIVE", issue["rows"][0]["verdict"])
        for candidate in (too_old, exact, inside, tag, issue):
            assert_closed(self, candidate)

    def test_unknown_activity_permission_and_history_dispatch_nothing(self) -> None:
        cases = {
            "activity": repo(
                activity="unknown",
                activity_reason="rate_limited",
            ),
            "permission": repo(permission="denied"),
            "history": repo(run_history="unknown"),
            "missing_push": repo(pushed_at=None),
        }
        expected = {
            "activity": "UNKNOWN_ACTIVITY",
            "permission": "UNKNOWN_PERMISSION",
            "history": "UNKNOWN_RUN_HISTORY",
            "missing_push": "UNKNOWN_ACTIVITY",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                plan = plan_for([body])
                row = plan["rows"][0]
                self.assertEqual(expected[name], row["verdict"])
                self.assertIs(True, row["alert"])
                self.assertEqual(0, plan["eligible_count"])
                self.assertEqual(1, nightly.plan_exit_code(plan))
                assert_closed(self, plan)

    def test_duplicate_key_is_suppressed(self) -> None:
        first = plan_for([repo()])
        key = first["rows"][0]["dispatch_key"]
        self.assertIsInstance(key, str)
        second = plan_for([repo(existing_dispatch_keys=[key])])
        self.assertEqual("SKIPPED_DUPLICATE", second["rows"][0]["verdict"])
        self.assertEqual(0, second["eligible_count"])
        assert_closed(self, first)
        assert_closed(self, second)
        paired = plan_for(
            [
                repo(name="one", repository_id=7),
                repo(name="two", repository_id=7, default_sha=SHA_B),
            ]
        )
        self.assertEqual(
            ["ELIGIBLE", "SKIPPED_DUPLICATE"],
            [row["verdict"] for row in paired["rows"]],
        )
        assert_closed(self, paired)

    def test_admission_skips_and_already_tested(self) -> None:
        cases = {
            "outside": (
                repo(owner="cryptoxdog", name="IB-Odoo_19"),
                "SKIPPED_NO_ADMISSION",
            ),
            "archived": (repo(archived=True), "SKIPPED_NO_ADMISSION"),
            "retired": (repo(lifecycle="retired"), "SKIPPED_NO_ADMISSION"),
            "lifecycle": (repo(lifecycle=None), "UNKNOWN"),
            "entrypoint": (repo(deep_workflow=None), "SKIPPED_NO_DEEP_ENTRYPOINT"),
            "tested": (
                repo(successful_sha=SHA_A, successful_contract=CONTRACT),
                "SKIPPED_ALREADY_TESTED",
            ),
        }
        for name, (body, verdict) in cases.items():
            with self.subTest(name=name):
                plan = plan_for([body])
                self.assertEqual(verdict, plan["rows"][0]["verdict"])
                self.assertEqual(0, plan["dispatch_count"])
                assert_closed(self, plan)

    def test_unreadable_registry_or_pagination_admits_nobody(self) -> None:
        for overrides in (
            {"registry_readable": False},
            {"pagination_complete": False},
        ):
            with self.subTest(overrides=overrides):
                plan = plan_for([repo()], **overrides)
                self.assertEqual(0, plan["eligible_count"])
                self.assertEqual(["UNKNOWN"], [row["verdict"] for row in plan["rows"]])
                self.assertEqual(1, nightly.plan_exit_code(plan))
                assert_closed(self, plan)

    def test_census_bound_does_not_emit_a_partial_fleet(self) -> None:
        plan = nightly.build_plan(
            fixture(
                [
                    repo(name="one"),
                    repo(name="two", repository_id=2),
                    repo(name="three", repository_id=3),
                ]
            ),
            mode="plan",
            event="schedule",
            max_repositories=2,
        )
        self.assertEqual(["UNKNOWN"], [row["verdict"] for row in plan["rows"]])
        self.assertEqual(0, plan["eligible_count"])
        assert_closed(self, plan)

    def test_apply_is_closed_and_schedule_forces_plan(self) -> None:
        applied = plan_for([repo()], mode="apply", event="workflow_dispatch")
        self.assertEqual("APPLY_CLOSED", applied["blocked_reason"])
        self.assertEqual("plan", applied["mode"])
        self.assertEqual(1, applied["eligible_count"])
        self.assertEqual(1, nightly.plan_exit_code(applied))
        assert_closed(self, applied)
        forced = plan_for([repo()], mode="apply", event="schedule")
        self.assertIsNone(forced["blocked_reason"])
        self.assertIs(True, forced["mode_forced"])
        self.assertEqual("plan", forced["mode"])
        self.assertEqual(0, nightly.plan_exit_code(forced))
        assert_closed(self, forced)

    def test_window_uses_evaluation_time_and_utc(self) -> None:
        late = plan_for(
            [],
            evaluated_at="2026-11-19T16:17:00+00:00",
            slot="2026-11-19",
        )
        self.assertEqual(9 * 3600, late["lateness_seconds"])
        self.assertEqual("2026-11-19T16:17:00Z", late["window_end"])
        self.assertEqual("2026-11-18T16:17:00Z", late["window_start"])
        self.assertFalse(late["live_schedule_receipt"])
        crossed = plan_for(
            [],
            evaluated_at="2026-11-20T01:00:00+00:00",
            slot="2026-11-20",
        )
        self.assertEqual("2026-11-19T07:17:00Z", crossed["cron_anchor"])
        self.assertGreater(crossed["lateness_seconds"], 0)
        spring = datetime(2026, 3, 8, 7, 17, tzinfo=timezone.utc)
        self.assertEqual(timedelta(hours=24), spring - (spring - timedelta(hours=24)))
        with self.assertRaises(nightly.FixtureError):
            plan_for([], evaluated_at="2026-11-19T07:17:00+02:00", slot="2026-11-19")

    def test_non_utc_timestamp_is_not_coerced(self) -> None:
        with self.assertRaises(nightly.FixtureError):
            nightly.build_plan(
                fixture(
                    [repo()],
                    evaluated_at="2026-11-19T09:17:00+02:00",
                    slot="2026-11-19",
                ),
                mode="plan",
                event="schedule",
            )

    def test_cli_fixture_is_not_a_live_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.json"
            output = Path(tmp) / "plan.json"
            path.write_text(
                json.dumps(fixture([repo()])),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "nightly_dispatch.py"),
                    "plan",
                    "--mode",
                    "plan",
                    "--event",
                    "schedule",
                    "--fixture",
                    str(path),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            plan = json.loads(output.read_text(encoding="utf-8"))
            assert_closed(self, plan)
            self.assertIn("live_schedule_receipt=false", completed.stdout)
            refused = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "nightly_dispatch.py"),
                    "plan",
                    "--mode",
                    "apply",
                    "--event",
                    "workflow_dispatch",
                    "--fixture",
                    str(path),
                    "--output",
                    str(Path(tmp) / "apply.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, refused.returncode)
            self.assertEqual(
                0,
                json.loads((Path(tmp) / "apply.json").read_text(encoding="utf-8"))[
                    "dispatch_count"
                ],
            )

    def test_scheduled_live_plan_reads_the_projection_and_exits_zero(self) -> None:
        """Witness for scheduled run 44770234202 (2026-11-20 16:07Z).

        The live path hard-coded ``registry_unreadable``, so every scheduled
        plan alerted and exited 1. It now reads Core's committed registry
        projection and the admitted entrypoints; apply is closed, so nothing
        is unknown and nothing is dispatched.
        """
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "nightly_dispatch.py"),
                    "plan",
                    "--mode",
                    "plan",
                    "--event",
                    "schedule",
                    "--live",
                    "--evaluated-at",
                    "2026-11-20T16:07:59Z",
                    "--output",
                    str(out),
                ],
                env={"PATH": "/usr/bin:/bin", "GITHUB_TOKEN": "", "GH_TOKEN": ""},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            plan = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("projection", plan["registry_status"])
        self.assertEqual("not_read", plan["metadata_status"])
        self.assertIs(False, plan["alert"])
        self.assertFalse(
            [row for row in plan["rows"] if row["verdict"].startswith("UNKNOWN")]
        )
        verdicts = {row["repository"]: row["verdict"] for row in plan["rows"]}
        self.assertEqual("SKIPPED_SELF_SCHEDULED", verdicts["Quantum-L9/l9-ci-core"])
        projection = nightly.load_registry_projection()
        for member in projection.members:
            self.assertIn(member, verdicts)
        assert_closed(self, plan)

    def test_dispatch_entrypoint_is_closed_not_unknown(self) -> None:
        projection = nightly.RegistryProjection(
            ("Quantum-L9/l9-ci-core", "Quantum-L9/alpha", "Quantum-L9/beta"), SHA_A
        )
        entries = (
            nightly.DeepEntrypoint(
                "Quantum-L9/l9-ci-core",
                ".github/workflows/nightly-deep.yml",
                "core-deep/v1",
                "self",
            ),
            nightly.DeepEntrypoint(
                "Quantum-L9/alpha", ".github/workflows/deep.yml", "a/v1", "dispatch"
            ),
            nightly.DeepEntrypoint(
                "Quantum-L9/gamma", ".github/workflows/deep.yml", "g/v1", "dispatch"
            ),
        )
        plan = nightly.plan_from_projection(
            projection,
            entries,
            evaluated_at=datetime(2026, 11, 20, 7, 17, tzinfo=timezone.utc),
            event="schedule",
            mode="plan",
        )
        verdicts = {row["repository"]: row["verdict"] for row in plan["rows"]}
        self.assertEqual(
            {
                "Quantum-L9/l9-ci-core": "SKIPPED_SELF_SCHEDULED",
                "Quantum-L9/alpha": "SKIPPED_APPLY_CLOSED",
                "Quantum-L9/beta": "SKIPPED_NO_DEEP_ENTRYPOINT",
                "Quantum-L9/gamma": "SKIPPED_NOT_PROJECTED",
            },
            verdicts,
        )
        self.assertEqual(0, nightly.plan_exit_code(plan))
        assert_closed(self, plan)

    def test_unreadable_projection_or_entrypoints_alert_and_dispatch_nothing(
        self,
    ) -> None:
        evaluated = datetime(2026, 11, 20, 7, 17, tzinfo=timezone.utc)
        receipt = json.loads(nightly.REGISTRY_PROJECTION.read_text(encoding="utf-8"))
        tampered = dict(receipt, members=receipt["members"][:-1])
        with tempfile.TemporaryDirectory() as tmp:
            cases = {
                "missing": (Path(tmp) / "absent.json", nightly.ENTRYPOINTS),
                "digest": (Path(tmp) / "tampered.json", nightly.ENTRYPOINTS),
                "entrypoints": (
                    nightly.REGISTRY_PROJECTION,
                    Path(tmp) / "entrypoints.json",
                ),
            }
            (Path(tmp) / "tampered.json").write_text(
                json.dumps(tampered), encoding="utf-8"
            )
            (Path(tmp) / "entrypoints.json").write_text(
                json.dumps(
                    {
                        "schema": "l9.nightly-deep-entrypoints/v1",
                        "entrypoints": [
                            {
                                "repository": "Quantum-L9/alpha",
                                "workflow": ".github/workflows/deep.yml",
                                "contract": "a/v1",
                                "scheduling": "self",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            for name, (projection_path, entrypoints_path) in cases.items():
                with self.subTest(name=name):
                    plan = nightly.live_plan(
                        evaluated_at=evaluated,
                        event="schedule",
                        mode="plan",
                        projection_path=projection_path,
                        entrypoints_path=entrypoints_path,
                    )
                    self.assertIs(True, plan["alert"])
                    self.assertEqual(1, nightly.plan_exit_code(plan))
                    self.assertEqual(0, plan["eligible_count"])
                    self.assertEqual(
                        ["UNKNOWN"], [row["verdict"] for row in plan["rows"]]
                    )
                    assert_closed(self, plan)

    def test_dispatch_key_is_a_bounded_token_consumers_accept(self) -> None:
        key = nightly.dispatch_key(
            repository_id=1,
            slot="2026-11-19",
            workflow=".github/workflows/deep-plan.yml",
            contract="ceg-deep/v1",
        )
        self.assertRegex(key, r"^nd1-[0-9a-f]{40}\Z")
        self.assertRegex(key, r"^[A-Za-z0-9._:-]{8,200}\Z")
        other = nightly.dispatch_key(
            repository_id=1,
            slot="2026-11-20",
            workflow=".github/workflows/deep-plan.yml",
            contract="ceg-deep/v1",
        )
        self.assertNotEqual(key, other)


class RunnerAndPullRequestTests(unittest.TestCase):
    def test_stale_or_malformed_sha_fails_before_a_heavy_run(self) -> None:
        stale = nightly.admit_runner(
            event="workflow_dispatch",
            mode="apply",
            expected_sha=SHA_B,
            observed_sha=SHA_A,
            utc_slot="2026-11-19",
            deep_contract=CONTRACT,
            dispatch_key="Quantum-L9|1|2026-11-19|wf|" + CONTRACT,
            repository="Quantum-L9/l9-ci-core",
            ref_name="main",
            default_branch="main",
        )
        self.assertEqual("STALE_SHA", stale.status)
        self.assertEqual(1, stale.exit_code)
        self.assertFalse(stale.heavy)
        self.assertFalse(stale.suite_pass)
        for malformed in (SHA_A.upper(), "refs/pull/12/merge", "abc", ""):
            with self.subTest(malformed=malformed):
                refused = nightly.admit_runner(
                    event="workflow_dispatch",
                    mode="apply",
                    expected_sha=malformed,
                    observed_sha=SHA_A,
                    utc_slot="2026-11-19",
                    deep_contract=CONTRACT,
                    dispatch_key="key",
                    repository="Quantum-L9/l9-ci-core",
                    ref_name="main",
                    default_branch="main",
                )
                self.assertEqual("MALFORMED_SHA", refused.status)
                self.assertEqual(1, refused.exit_code)
                self.assertFalse(refused.heavy)
                self.assertFalse(refused.suite_pass)

    def test_plan_duplicate_and_unknown_history_are_not_a_suite_pass(self) -> None:
        planned = nightly.admit_runner(
            event="workflow_dispatch",
            mode="plan",
            expected_sha=SHA_A,
            observed_sha=SHA_B,
            utc_slot="",
            deep_contract="",
            dispatch_key="",
            repository="Quantum-L9/l9-ci-core",
            ref_name="feature",
            default_branch="main",
        )
        self.assertEqual("PLAN_ONLY", planned.status)
        self.assertEqual(0, planned.exit_code)
        self.assertFalse(planned.heavy)
        self.assertFalse(planned.suite_pass)
        key = "Quantum-L9|1|2026-11-19|wf|" + CONTRACT
        duplicate = nightly.admit_runner(
            event="workflow_dispatch",
            mode="apply",
            expected_sha=SHA_A,
            observed_sha=SHA_A,
            utc_slot="2026-11-19",
            deep_contract=CONTRACT,
            dispatch_key=key,
            repository="Quantum-L9/l9-ci-core",
            ref_name="main",
            default_branch="main",
            seen_dispatch_keys=frozenset({key}),
        )
        self.assertEqual("SKIPPED_DUPLICATE", duplicate.status)
        self.assertFalse(duplicate.heavy)
        self.assertFalse(duplicate.suite_pass)
        unknown = nightly.admit_runner(
            event="workflow_dispatch",
            mode="apply",
            expected_sha=SHA_A,
            observed_sha=SHA_A,
            utc_slot="2026-11-19",
            deep_contract=CONTRACT,
            dispatch_key=key,
            repository="Quantum-L9/l9-ci-core",
            ref_name="main",
            default_branch="main",
            run_history="unknown",
        )
        self.assertEqual("UNKNOWN_RUN_HISTORY", unknown.status)
        self.assertEqual(1, unknown.exit_code)
        self.assertFalse(unknown.heavy)
        admitted = nightly.admit_runner(
            event="workflow_dispatch",
            mode="apply",
            expected_sha=SHA_A,
            observed_sha=SHA_A,
            utc_slot="2026-11-19",
            deep_contract=CONTRACT,
            dispatch_key=key,
            repository="Quantum-L9/l9-ci-core",
            ref_name="main",
            default_branch="main",
            run_history="known",
        )
        self.assertEqual("APPLY_ADMITTED", admitted.status)
        self.assertTrue(admitted.heavy)
        self.assertFalse(admitted.suite_pass)

    def test_pull_request_scope_failure_selects_no_suite(self) -> None:
        cases = (
            ("pull_request", "", "", "NO_FULL_SUITE_FALLBACK", 0),
            ("merge_group", "", "", "NO_FULL_SUITE_FALLBACK", 0),
            ("merge", "", "", "NO_FULL_SUITE_FALLBACK", 0),
            ("pull_request", "1", "", "POLICY_PR_DEEP_FORBIDDEN", 1),
            ("pull_request", "true", "resolved", "POLICY_PR_DEEP_FORBIDDEN", 1),
            ("pull_request", "", "unknown", "PR_SCOPE_UNKNOWN", 1),
            ("pull_request", "", "failed", "PR_SCOPE_UNKNOWN", 1),
            ("push", "1", "unknown", "NOT_APPLICABLE", 0),
        )
        for event, override, scope, status, code in cases:
            with self.subTest(event=event, override=override, scope=scope):
                decision = nightly.pr_path_decision(
                    event=event,
                    full_suite_override=override,
                    scope_status=scope,
                )
                self.assertEqual(status, decision.status)
                self.assertEqual(code, decision.exit_code)
                self.assertFalse(decision.heavy)
                self.assertFalse(decision.suite_pass)
                self.assertEqual("none", decision.selected_suite)


class WorkflowContractTests(unittest.TestCase):
    def test_embedded_gates_match_the_module(self) -> None:
        nightly_workflow = load(NIGHTLY)
        scripts = [
            extract_python(str(step["run"]))
            for job in nightly_workflow["jobs"].values()
            for step in job["steps"]
            if "<<'PY'" in str(step.get("run", ""))
        ]
        self.assertEqual(2, len(scripts))
        for script in scripts:
            self.assertEqual(nightly.DISPATCH_GATE_PYTHON, script)
        org = load(ORG_CI)
        guards = [
            extract_python(str(step["run"]))
            for job in org["jobs"].values()
            for step in job["steps"]
            if step.get("name") == "Refuse a pull-request full-suite fallback"
        ]
        self.assertEqual([nightly.PR_GUARD_PYTHON], guards)

    def test_scheduler_is_plan_only_and_keeps_the_deep_cron(self) -> None:
        scheduler = load(SCHEDULER)
        nightly_workflow = load(NIGHTLY)
        scheduler_on = triggers_of(scheduler)
        nightly_on = triggers_of(nightly_workflow)
        self.assertEqual([{"cron": nightly.NIGHTLY_CRON}], scheduler_on["schedule"])
        self.assertEqual([{"cron": nightly.NIGHTLY_CRON}], nightly_on["schedule"])
        # Operator decision 2026-11-20 (AGENTS.md §37): apply is open. The plan
        # job still only plans; the apply job alone holds the App private key.
        self.assertEqual(
            ["plan", "apply"],
            scheduler_on["workflow_dispatch"]["inputs"]["mode"]["options"],
        )
        text = SCHEDULER.read_text(encoding="utf-8")
        plan_text = yaml.safe_dump(scheduler["jobs"]["plan"])
        self.assertNotIn("secrets.", plan_text)
        self.assertNotIn("nightly_apply.py", plan_text)
        self.assertEqual(["plan"], [scheduler["jobs"]["apply"]["needs"]])
        self.assertNotIn("make test", text)
        self.assertNotIn("make setup", text)
        self.assertNotIn("pip install", text)
        self.assertNotIn("pytest", text)
        self.assertNotIn("unittest", text)
        self.assertIn("--mode plan", text)
        self.assertIn("--live \\\n", text)
        self.assertNotIn("--live-metadata", text)
        self.assertNotIn("--fixture", text)
        self.assertIn('REPOSITORY}" != "Quantum-L9/l9-ci-core"', text)
        self.assertEqual({"contents": "read"}, scheduler["permissions"])
        publish = next(
            step
            for step in scheduler["jobs"]["plan"]["steps"]
            if step.get("name") == "Publish the plan"
        )
        self.assertEqual("always()", str(publish["if"]))
        self.assertIn("nightly-plan.json", str(publish["env"]["PLAN"]))
        self.assertIn("GITHUB_STEP_SUMMARY", str(publish["run"]))
        self.assertNotIn("upload-artifact", text)
        guard = next(
            step
            for job in load(ORG_CI)["jobs"].values()
            for step in job["steps"]
            if step.get("name") == "Refuse a pull-request full-suite fallback"
        )
        self.assertEqual(
            "${{ steps.runtime.outputs.event-class }}",
            guard["env"]["GH_EVENT"],
        )
        deep = NIGHTLY.read_text(encoding="utf-8")
        self.assertIn("make test-deep", deep)
        self.assertNotIn("pull_request:", deep)
        self.assertNotIn("pull_request:", text)

    def test_deep_dispatch_contract_gates_heavy_steps(self) -> None:
        workflow = load(NIGHTLY)
        inputs = triggers_of(workflow)["workflow_dispatch"]["inputs"]
        self.assertEqual(
            ["expected_sha", "utc_slot", "deep_contract", "dispatch_key", "mode"],
            list(inputs),
        )
        self.assertEqual("plan", inputs["mode"]["default"])
        self.assertEqual(["plan", "apply"], inputs["mode"]["options"])
        job = workflow["jobs"]["test-deep"]
        self.assertEqual("Nightly deep tests", job["name"])
        self.assertIn("inputs.mode == 'apply'", " ".join(str(job["if"]).split()))
        names = [step["name"] for step in job["steps"]]
        self.assertLess(
            names.index("Reject an unadmitted dispatch"),
            names.index("Checkout immutable event revision"),
        )
        self.assertLess(
            names.index("Checkout immutable event revision"),
            names.index("make test-deep"),
        )
        self.assertLess(
            names.index("Checkout immutable event revision"),
            names.index("Gate on a successful deep receipt"),
        )
        self.assertLess(
            names.index("Gate on a successful deep receipt"),
            names.index("Set up Python"),
        )
        for step in job["steps"]:
            if step["name"] in {
                "Checkout immutable event revision",
                "Gate on a successful deep receipt",
            }:
                self.assertEqual(HEAVY_IF, " ".join(str(step["if"]).split()))
            if step["name"] in {"Set up Python", "make setup", "make test-deep"}:
                self.assertEqual("steps.receipt.outputs.run == 'true'", str(step["if"]))
        gate = next(
            step
            for step in job["steps"]
            if step["name"] == "Gate on a successful deep receipt"
        )
        self.assertEqual("receipt", gate["id"])
        self.assertIn("tools/nightly_apply.py receipt", gate["run"])
        self.assertEqual("${{ github.token }}", gate["env"]["L9_RECEIPT_TOKEN"])
        self.assertEqual({"contents": "read", "actions": "read"}, job["permissions"])
        plan_job = workflow["jobs"]["dispatch-plan"]
        self.assertNotIn("make ", str(plan_job))
        self.assertNotIn("git ", str(plan_job))
        self.assertIn("inputs.mode != 'apply'", str(plan_job["if"]))

    def test_required_check_and_make_v1_stay_in_place(self) -> None:
        org = load(ORG_CI)
        names = [job.get("name", job_id) for job_id, job in org["jobs"].items()]
        self.assertIn("Analyze (central Core)", names)
        self.assertEqual(
            {
                "schema": "l9.repo-execution/v2",
                "facade": "make-v1",
                "required_phases": ["setup", "validate", "check", "test"],
            },
            json.loads(REPO_WORKFLOW.read_text(encoding="utf-8")),
        )
        text = ORG_CI.read_text(encoding="utf-8")
        self.assertNotIn("unittest discover", text)
        self.assertNotIn("test-deep", text)
        self.assertNotIn("make test-deep", text)

    def test_planner_source_reads_no_token(self) -> None:
        source = (ROOT / "tools" / "nightly_dispatch.py").read_text(encoding="utf-8")
        self.assertNotIn("GITHUB_TOKEN", source)
        self.assertNotIn("Authorization", source)
        self.assertNotIn("secrets.", source)


if __name__ == "__main__":
    unittest.main()
