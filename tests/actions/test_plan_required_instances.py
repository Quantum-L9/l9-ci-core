"""The SDK inventory becomes Core's instance plan without dropping a language.

The fixtures under ``tests/fixtures/capability-inventory/`` are records the
SDK's ``l9-ci providers inventory --format json`` printed for four small
repositories at ``l9.capability-inventory/v1`` (lane L1, SDK pull request
119). Core tests the contract, not an SDK revision: nothing here imports the
SDK or pins it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ROOT / ".github" / "actions"
FIXTURES = ROOT / "tests" / "fixtures" / "capability-inventory"
SDK = "b" * 40
REPOSITORY = "Quantum-L9/example"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


planner = _load("plan_required_instances", ACTIONS / "plan-required-instances/plan.py")
aggregator = _load(
    "aggregate_required_instances_for_plan",
    ACTIONS / "aggregate-required-instances/aggregate.py",
)
TARGETS = planner.load_executable_targets()


def inventory(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def revision_of(record: dict[str, Any]) -> str:
    return planner.unwrap(record)["snapshot"]["revision"]


def plan_for(
    record: dict[str, Any],
    *,
    required: tuple[str, ...] = ("semgrep",),
    **overrides: Any,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "repository": REPOSITORY,
        "sdk_revision": SDK,
        "required_providers": frozenset(required),
        "executable_targets": TARGETS,
    }
    arguments.update(overrides)
    if "revision" not in arguments:
        arguments["revision"] = revision_of(record)
    return planner.build_plan(record, **arguments)


def result_record(
    plan: dict[str, Any], target: str, status: str = "pass"
) -> dict[str, Any]:
    return {
        "schema": "l9.core-instance-result/v1",
        "repository": plan["repository"],
        "provider": "semgrep",
        "target": target,
        "revision": plan["revision"],
        "sdk_revision": plan["sdk_revision"],
        "status": status,
    }


def aggregate(plan: dict[str, Any], *targets: str) -> dict[str, Any]:
    records = [(f"{t}.json", result_record(plan, t)) for t in targets]
    return aggregator.aggregate(aggregator.parse_plan(plan), records)


class PlanFromRecordedInventoryTests(unittest.TestCase):
    def test_known_good_mixed_repository_plans_both_languages(self) -> None:
        plan = plan_for(inventory("mixed"))
        self.assertEqual(
            [("semgrep", "language:python"), ("semgrep", "language:typescript")],
            [(i["provider"], i["target"]) for i in plan["instances"]],
        )
        self.assertTrue(all(i["required"] for i in plan["instances"]))
        self.assertNotIn("unresolved", plan)
        self.assertNotIn("not_applicable", plan)
        both = aggregate(plan, "language:python", "language:typescript")
        self.assertEqual(("pass", 2), (both["status"], both["completed"]))

    def test_known_bad_mixed_repository_with_one_language_run_is_incomplete(
        self,
    ) -> None:
        """The defect this replaces: a preferred language hid the other one."""
        plan = plan_for(inventory("mixed"))
        for only in ("language:python", "language:typescript"):
            with self.subTest(only=only):
                result = aggregate(plan, only)
                self.assertEqual("incomplete", result["status"])
                self.assertEqual(1, result["completed"])

    def test_single_language_repository_plans_one_and_lists_the_other(self) -> None:
        plan = plan_for(inventory("python-only"))
        self.assertEqual(["language:python"], [i["target"] for i in plan["instances"]])
        self.assertEqual(
            ["semgrep:language:typescript"], [i["id"] for i in plan["not_planned"]]
        )
        self.assertEqual("pass", aggregate(plan, "language:python")["status"])

    def test_no_recognized_source_is_not_applicable_with_the_sdk_reason(self) -> None:
        plan = plan_for(inventory("none"))
        self.assertEqual([], plan["instances"])
        self.assertIn("no source file", plan["not_applicable"]["reason"])
        result = aggregate(plan)
        self.assertEqual("pass", result["status"])
        self.assertIn("no source file", result["not_applicable_reason"])

    def test_unsupported_language_stays_visible_and_is_not_planned(self) -> None:
        plan = plan_for(inventory("unsupported"))
        self.assertEqual([], plan["instances"])
        self.assertEqual(["go"], plan["unsupported_languages"])
        self.assertIn("language go", plan["not_applicable"]["reason"])

    def test_requiredness_comes_only_from_core(self) -> None:
        record = inventory("mixed")
        self.assertNotIn("required", json.dumps(planner.unwrap(record)))
        advisory = plan_for(record, required=())
        self.assertEqual([False, False], [i["required"] for i in advisory["instances"]])
        self.assertEqual("pass", aggregate(advisory)["status"])
        narrowed = plan_for(
            record, executable_targets={"semgrep": frozenset({"language:python"})}
        )
        by_target = {i["target"]: i for i in narrowed["instances"]}
        self.assertTrue(by_target["language:python"]["required"])
        self.assertFalse(by_target["language:typescript"]["required"])
        self.assertIn(
            "does not execute", by_target["language:typescript"]["requiredness"]
        )

    def test_executable_targets_match_the_identity_maps_core_ships(self) -> None:
        maps = sorted(
            f"language:{path.stem}"
            for path in (ACTIONS / "resolve-governance/identity-maps").glob("*.yaml")
        )
        self.assertEqual(maps, sorted(TARGETS["semgrep"]))

    def test_bare_record_and_cli_envelope_plan_the_same(self) -> None:
        envelope = inventory("mixed")
        self.assertEqual(plan_for(envelope), plan_for(envelope["result"]))


class UnresolvedInventoryTests(unittest.TestCase):
    def mutated(self, **changes: Any) -> dict[str, Any]:
        record = copy.deepcopy(inventory("mixed")["result"])
        record.update(changes)
        return record

    def test_known_bad_unmet_prerequisite_can_never_pass(self) -> None:
        for state in ("incomplete", "failed"):
            with self.subTest(state=state):
                record = self.mutated()
                record["provider_prerequisites"][1]["state"] = state
                plan = plan_for(record)
                self.assertEqual(state, plan["instances"][1]["prerequisite"])
                self.assertTrue(plan["instances"][1]["required"])
                result = aggregate(plan, "language:python", "language:typescript")
                self.assertEqual("incomplete", result["status"])

    def test_known_bad_failed_or_incomplete_inventory_is_unresolved(self) -> None:
        for status in ("failed", "incomplete"):
            with self.subTest(status=status):
                plan = plan_for(
                    self.mutated(status=status, reasons=["manifest x: bad"])
                )
                self.assertIn(f"status is {status}", plan["unresolved"][0])
                result = aggregate(plan, "language:python", "language:typescript")
                self.assertEqual("incomplete", result["status"])

    def test_known_bad_inventory_of_another_revision_is_unresolved(self) -> None:
        record = inventory("mixed")
        for name, revision in {"another revision": "c" * 40}.items():
            with self.subTest(case=name):
                plan = plan_for(record, revision=revision)
                self.assertIn(
                    "not bound to the evaluated revision", plan["unresolved"][0]
                )
        unbound = self.mutated()
        unbound["snapshot"] = {**unbound["snapshot"], "revision": None}
        plan = plan_for(unbound, revision="c" * 40)
        self.assertIn("not bound", plan["unresolved"][0])

    def test_known_bad_unresolved_empty_plan_does_not_pass(self) -> None:
        record = copy.deepcopy(inventory("none")["result"])
        record["status"] = "failed"
        plan = plan_for(record)
        self.assertEqual([], plan["instances"])
        self.assertEqual("incomplete", aggregate(plan)["status"])

    def test_malformed_inventory_is_refused_not_repaired(self) -> None:
        good = inventory("mixed")["result"]

        def entry(**changes: Any) -> dict[str, Any]:
            record = copy.deepcopy(good)
            record["provider_prerequisites"][0].update(changes)
            return record

        duplicated = copy.deepcopy(good)
        duplicated["provider_prerequisites"].append(
            duplicated["provider_prerequisites"][0]
        )
        cases: dict[str, Any] = {
            "not an object": [],
            "wrong schema": {**good, "schema": "l9.capability-inventory/v2"},
            "failed envelope": {"ok": False, "result": good},
            "unknown status": {**good, "status": "ok"},
            "no reasons": {**good, "reasons": None},
            "no snapshot": {**good, "snapshot": None},
            "prerequisites not a list": {**good, "provider_prerequisites": {}},
            "unknown prerequisite state": entry(state="skipped"),
            "malformed target": entry(target="python"),
            "malformed provider": entry(provider_id="Sem grep"),
            "no reason": entry(reason=""),
            "duplicate prerequisite": duplicated,
        }
        for name, record in cases.items():
            with self.subTest(case=name), self.assertRaises(planner.PlanningError):
                plan_for(record, revision=good["snapshot"]["revision"])

    def test_malformed_inputs_are_refused(self) -> None:
        record = inventory("mixed")
        for name, overrides in {
            "repository": {"repository": "not-a-repository"},
            "revision": {"revision": "abc"},
            "sdk revision": {"sdk_revision": ""},
            "required provider": {"required_providers": frozenset({"Sem grep"})},
        }.items():
            with self.subTest(case=name), self.assertRaises(planner.PlanningError):
                plan_for(record, **overrides)


class EntrypointTests(unittest.TestCase):
    def invoke(self, workspace: Path, **env: str) -> tuple[int, dict[str, str], str]:
        output = workspace / "github-output"
        record = inventory("mixed")
        (workspace / "inventory.json").write_text(json.dumps(record), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(ACTIONS / "plan-required-instances/plan.py")],
            env={
                **os.environ,
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_OUTPUT": str(output),
                "L9_PLAN_INVENTORY": "inventory.json",
                "L9_PLAN_REPOSITORY": REPOSITORY,
                "L9_PLAN_REVISION": revision_of(record),
                "L9_PLAN_SDK_REVISION": SDK,
                "L9_PLAN_REQUIRED_PROVIDERS": "semgrep",
                "L9_PLAN_OUTPUT": "out/plan.json",
                **env,
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
        return completed.returncode, outputs, completed.stdout + completed.stderr

    def test_plan_is_written_once_and_the_aggregate_accepts_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            code, outputs, text = self.invoke(workspace)
            self.assertEqual(0, code, text)
            self.assertEqual("out/plan.json", outputs["plan-path"])
            self.assertEqual(
                "semgrep:language:python,semgrep:language:typescript",
                outputs["targets"],
            )
            self.assertEqual("2", outputs["required-count"])
            plan = json.loads((workspace / "out/plan.json").read_text("utf-8"))
            self.assertEqual(2, len(aggregator.parse_plan(plan).instances))
            code, _, text = self.invoke(workspace)
            self.assertEqual(2, code)
            self.assertIn("written once", text)

    def test_unresolved_plan_is_announced_and_bad_input_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            code, _, text = self.invoke(workspace, L9_PLAN_REVISION="c" * 40)
            self.assertEqual(0, code, text)
            self.assertIn("::error::plan is unresolved", text)
        with tempfile.TemporaryDirectory() as temp:
            code, outputs, text = self.invoke(Path(temp), L9_PLAN_REVISION="short")
            self.assertEqual(2, code)
            self.assertNotIn("plan-path", outputs)


class BoundaryTests(unittest.TestCase):
    def test_planner_reads_no_canonical_sdk_artifact_and_imports_no_sdk(self) -> None:
        text = (ACTIONS / "plan-required-instances/plan.py").read_text("utf-8").lower()
        for prohibited in (
            "finding-bundle.json",
            "gate-result.json",
            '"findings"',
            "severity",
            "import l9_ci",
            "from l9_ci",
        ):
            with self.subTest(value=prohibited):
                self.assertNotIn(prohibited, text)

    def test_recorded_fixtures_are_the_contract_schema(self) -> None:
        names = sorted(path.stem for path in FIXTURES.glob("*.json"))
        self.assertEqual(["mixed", "none", "python-only", "unsupported"], names)
        for name in names:
            with self.subTest(fixture=name):
                record = planner.unwrap(inventory(name))
                self.assertEqual("l9.capability-inventory/v1", record["schema"])
                self.assertRegex(record["schema_version"], r"^1\.")


if __name__ == "__main__":
    unittest.main()
