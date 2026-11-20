"""Every required instance is accounted for, or the aggregate is not a pass.

The mixed-language fixture (Python plus TypeScript) is the known-good case:
two completed executions and one aggregate. Each known-bad case removes,
kills or corrupts one instance and must turn the aggregate red.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "aggregate-required-instances"
MODULE_PATH = ACTION / "aggregate.py"
FIXTURE = ROOT / "tests" / "fixtures" / "required-instances" / "mixed"

spec = importlib.util.spec_from_file_location(
    "aggregate_required_instances", MODULE_PATH
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def fixture_plan() -> dict[str, Any]:
    return json.loads((FIXTURE / "plan.json").read_text(encoding="utf-8"))


def fixture_records() -> dict[str, dict[str, Any]]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((FIXTURE / "results").glob("*.json"))
    }


def run(plan: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    return module.aggregate(module.parse_plan(plan), list(records.items()))


class MixedLanguageFixtureTests(unittest.TestCase):
    def test_known_good_two_completed_executions_and_one_aggregate(self) -> None:
        result = run(fixture_plan(), fixture_records())
        self.assertEqual("pass", result["status"])
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(result["accounted"])
        self.assertEqual(2, result["planned"])
        self.assertEqual(2, result["completed"])
        self.assertEqual(
            ["semgrep:language:python", "semgrep:language:typescript"],
            [row["id"] for row in result["instances"]],
        )
        self.assertEqual([], result["reasons"])

    def test_known_bad_one_instance_removed_turns_the_aggregate_red(self) -> None:
        for removed in ("semgrep-python.json", "semgrep-typescript.json"):
            with self.subTest(removed=removed):
                records = fixture_records()
                del records[removed]
                result = run(fixture_plan(), records)
                self.assertEqual("incomplete", result["status"])
                self.assertEqual(6, result["exit_code"])
                self.assertFalse(result["accounted"])
                self.assertEqual(1, result["completed"])
                self.assertIn("is missing", " ".join(result["reasons"]))

    def test_known_bad_killed_instance_is_never_a_pass(self) -> None:
        for status in ("skipped", "timed_out", "cancelled", "incomplete", "", "ok"):
            with self.subTest(status=status):
                records = fixture_records()
                records["semgrep-typescript.json"]["status"] = status
                result = run(fixture_plan(), records)
                self.assertEqual("incomplete", result["status"])
                self.assertFalse(result["accounted"])
                self.assertEqual(1, result["completed"])

    def test_failed_instance_fails_the_aggregate(self) -> None:
        records = fixture_records()
        records["semgrep-python.json"]["status"] = "fail"
        result = run(fixture_plan(), records)
        self.assertEqual("fail", result["status"])
        self.assertEqual(1, result["exit_code"])
        self.assertEqual(2, result["completed"])

    def test_failure_outranks_a_missing_instance(self) -> None:
        records = fixture_records()
        records["semgrep-python.json"]["status"] = "fail"
        del records["semgrep-typescript.json"]
        result = run(fixture_plan(), records)
        self.assertEqual("fail", result["status"])
        self.assertFalse(result["accounted"])

    def test_known_bad_untrusted_records_do_not_count(self) -> None:
        mutations: dict[str, dict[str, Any]] = {
            "another revision": {"revision": "c" * 40},
            "another SDK": {"sdk_revision": "c" * 40},
            "another repository": {"repository": "Quantum-L9/other"},
            "wrong schema": {"schema": "l9.core-instance-result/v0"},
            "no status": {"status": None},
        }
        for name, change in mutations.items():
            with self.subTest(case=name):
                records = fixture_records()
                records["semgrep-python.json"].update(change)
                result = run(fixture_plan(), records)
                self.assertEqual("incomplete", result["status"])
                self.assertEqual(1, len(result["untrusted_records"]))
                self.assertEqual(1, result["completed"])

    def test_known_bad_unplanned_duplicate_and_malformed_records(self) -> None:
        records = fixture_records()
        extra = copy.deepcopy(records["semgrep-python.json"])
        extra["target"] = "language:go"
        with self.subTest(case="unplanned instance"):
            result = run(fixture_plan(), {**records, "semgrep-go.json": extra})
            self.assertEqual("incomplete", result["status"])
            self.assertEqual(2, result["completed"])
        with self.subTest(case="two records for one instance"):
            duplicate = copy.deepcopy(records["semgrep-python.json"])
            result = run(
                fixture_plan(), {**records, "semgrep-python-2.json": duplicate}
            )
            self.assertEqual("incomplete", result["status"])
            self.assertEqual(1, result["completed"])
        with self.subTest(case="not an object"):
            result = run(fixture_plan(), {**records, "junk.json": ["pass"]})
            self.assertEqual("incomplete", result["status"])

    def test_optional_instance_does_not_gate_but_stays_visible(self) -> None:
        plan = fixture_plan()
        plan["instances"][1]["required"] = False
        records = fixture_records()
        del records["semgrep-typescript.json"]
        result = run(plan, records)
        self.assertEqual("pass", result["status"])
        self.assertEqual("missing", result["instances"][1]["status"])
        self.assertFalse(result["instances"][1]["required"])

    def test_untrusted_record_for_an_optional_instance_does_not_gate(self) -> None:
        plan = fixture_plan()
        plan["instances"][1]["required"] = False
        for name, change in {
            "stale revision": {"revision": "c" * 40},
            "another SDK": {"sdk_revision": "c" * 40},
            "no status": {"status": None},
        }.items():
            with self.subTest(case=name):
                records = fixture_records()
                records["semgrep-typescript.json"].update(change)
                result = run(plan, records)
                self.assertEqual("pass", result["status"])
                self.assertTrue(result["accounted"])
                self.assertEqual([], result["reasons"])
                self.assertEqual("missing", result["instances"][1]["status"])
                self.assertEqual(1, len(result["untrusted_records"]))
                self.assertFalse(result["untrusted_records"][0]["gating"])

    def test_known_bad_untrusted_record_still_gates_when_not_optional(self) -> None:
        plan = fixture_plan()
        plan["instances"][1]["required"] = False
        records = fixture_records()
        cases: dict[str, dict[str, Any]] = {
            "stale record for the required instance": {
                **records,
                "semgrep-python.json": {
                    **records["semgrep-python.json"],
                    "revision": "c" * 40,
                },
            },
            "record for an unplanned instance": {
                **records,
                "x.json": {**records["semgrep-python.json"], "target": "language:go"},
            },
            "unreadable record": {**records, "junk.json": "unreadable: bad JSON"},
        }
        for name, mutated in cases.items():
            with self.subTest(case=name):
                result = run(plan, mutated)
                self.assertEqual("incomplete", result["status"])
                self.assertTrue(
                    [entry for entry in result["untrusted_records"] if entry["gating"]]
                )

    def test_known_bad_unmet_prerequisite_is_never_a_pass(self) -> None:
        """A record cannot rescue an instance the SDK says cannot run."""
        for state in ("incomplete", "failed"):
            with self.subTest(prerequisite=state):
                plan = fixture_plan()
                plan["instances"][1]["prerequisite"] = state
                result = run(plan, fixture_records())
                self.assertEqual("incomplete", result["status"])
                self.assertEqual(
                    f"prerequisite_{state}", result["instances"][1]["status"]
                )
                self.assertEqual(1, result["completed"])

    def test_known_bad_unresolved_plan_is_never_a_pass(self) -> None:
        plan = fixture_plan()
        plan["unresolved"] = ["SDK capability inventory status is failed"]
        result = run(plan, fixture_records())
        self.assertEqual("incomplete", result["status"])
        self.assertFalse(result["accounted"])
        self.assertEqual(2, result["completed"])
        self.assertIn("plan is unresolved", " ".join(result["reasons"]))

    def test_empty_plan_needs_evidence_of_non_applicability(self) -> None:
        plan = fixture_plan()
        plan["instances"] = []
        self.assertEqual("incomplete", run(plan, {})["status"])
        plan["not_applicable"] = {"reason": "SDK detected no supported language"}
        result = run(plan, {})
        self.assertEqual("pass", result["status"])
        self.assertEqual(0, result["completed"])
        self.assertIn("no supported language", result["not_applicable_reason"])


class PlanValidationTests(unittest.TestCase):
    def test_unusable_plans_are_refused(self) -> None:
        def changed(**changes: Any) -> dict[str, Any]:
            return {**fixture_plan(), **changes}

        duplicate = fixture_plan()
        duplicate["instances"].append(duplicate["instances"][0])
        cases: dict[str, Any] = {
            "not an object": [],
            "wrong schema": changed(schema="l9.core-required-instances/v0"),
            "short revision": changed(revision="abc123"),
            "no SDK revision": changed(sdk_revision=""),
            "instances not a list": changed(instances={}),
            "duplicate instance": duplicate,
            "unsafe language": changed(
                instances=[
                    {"provider": "semgrep", "language": "../x", "required": True}
                ]
            ),
            "required not boolean": changed(
                instances=[{"provider": "semgrep", "language": "go", "required": "no"}]
            ),
            "not applicable with instances": changed(not_applicable={"reason": "x"}),
            "not applicable without reason": changed(instances=[], not_applicable={}),
        }
        for name, plan in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(module.PlanError):
                    module.parse_plan(plan)


class EntrypointTests(unittest.TestCase):
    def invoke(self, workspace: Path) -> tuple[int, dict[str, str], str]:
        output = workspace / "github-output"
        summary = workspace / "summary"
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            env={
                **os.environ,
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
                "L9_INSTANCE_PLAN": "plan.json",
                "L9_INSTANCE_RESULTS": "results",
                "L9_INSTANCE_AGGREGATE": "out/aggregate.json",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        outputs = {}
        if output.is_file():
            for line in output.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                outputs[key] = value
        text = summary.read_text(encoding="utf-8") if summary.is_file() else ""
        return completed.returncode, outputs, text + completed.stderr

    def test_fixture_then_the_same_fixture_with_one_instance_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "good"
            shutil.copytree(FIXTURE, workspace)
            code, outputs, summary = self.invoke(workspace)
            self.assertEqual(0, code, summary)
            self.assertEqual("pass", outputs["status"])
            self.assertEqual("0", outputs["exit-code"])
            self.assertEqual("true", outputs["accounted"])
            self.assertEqual("out/aggregate.json", outputs["aggregate-path"])
            self.assertIn("completed executions: 2", summary)
            written = json.loads((workspace / "out/aggregate.json").read_text())
            self.assertEqual("l9.core-instance-aggregate/v1", written["schema"])

            workspace = Path(temp) / "bad"
            shutil.copytree(FIXTURE, workspace)
            (workspace / "results" / "semgrep-typescript.json").unlink()
            code, outputs, summary = self.invoke(workspace)
            self.assertEqual(0, code, summary)
            self.assertEqual("incomplete", outputs["status"])
            self.assertEqual("6", outputs["exit-code"])
            self.assertEqual("false", outputs["accounted"])
            self.assertIn("semgrep:language:typescript is missing", summary)

    def test_unreadable_record_and_missing_results_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "corrupt"
            shutil.copytree(FIXTURE, workspace)
            (workspace / "results" / "semgrep-python.json").write_text("{", "utf-8")
            code, outputs, _ = self.invoke(workspace)
            self.assertEqual(0, code)
            self.assertEqual("incomplete", outputs["status"])

            workspace = Path(temp) / "none"
            shutil.copytree(FIXTURE, workspace)
            shutil.rmtree(workspace / "results")
            code, outputs, _ = self.invoke(workspace)
            self.assertEqual("incomplete", outputs["status"])
            self.assertEqual("false", outputs["accounted"])

    def test_unusable_plan_and_existing_output_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "noplan"
            shutil.copytree(FIXTURE, workspace)
            (workspace / "plan.json").unlink()
            code, outputs, text = self.invoke(workspace)
            self.assertEqual(2, code)
            self.assertNotIn("status", outputs)
            self.assertIn("plan is unreadable", text)

            workspace = Path(temp) / "rewrite"
            shutil.copytree(FIXTURE, workspace)
            (workspace / "out").mkdir()
            (workspace / "out" / "aggregate.json").write_text("{}", "utf-8")
            code, outputs, text = self.invoke(workspace)
            self.assertEqual(2, code)
            self.assertIn("written once", text)


class BoundaryTests(unittest.TestCase):
    def test_aggregator_reads_no_canonical_sdk_artifact(self) -> None:
        text = MODULE_PATH.read_text(encoding="utf-8").lower()
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        for prohibited in (
            "finding-bundle.json",
            "gate-result.json",
            "agent-review-payload.json",
            '"findings"',
            '"classifications"',
            "severity",
        ):
            with self.subTest(value=prohibited):
                self.assertNotIn(prohibited, code)

    def test_action_declares_its_outputs(self) -> None:
        text = (ACTION / "action.yml").read_text(encoding="utf-8")
        for name in ("status:", "exit-code:", "accounted:", "aggregate-path:"):
            self.assertIn(name, text)
        self.assertIn("using: composite", text)


if __name__ == "__main__":
    unittest.main()
