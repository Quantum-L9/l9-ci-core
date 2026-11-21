"""The required job plans, runs and aggregates every instance (L9-CI-CORE-003).

The required check (``.l9/org-runtime-contract.yaml``) used to run Semgrep for the one detected language.
It now inventories the snapshot with the SDK, plans every applicable
provider/language instance with ``plan-required-instances``, runs each one, and
enforces the ``aggregate-required-instances`` verdict. These tests execute the
runner script org-ci.yml embeds, against a fake SDK, with Core's real planner
and aggregator, and check the wiring of the job itself.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
ACTIONS = ROOT / ".github" / "actions"
INVENTORY = ROOT / "tests" / "fixtures" / "capability-inventory" / "mixed.json"
SDK = "d" * 40

FAKE_SDK = """\
#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ["FAKE_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\\n")
language = args[args.index("--language") + 1] if "--language" in args else None
if args[:2] == ["semgrep", "run"]:
    out = pathlib.Path(args[args.index("--output") + 1])
    out.write_text("{}", encoding="utf-8")
    raise SystemExit(int(os.environ.get("FAKE_RUN_" + language.upper(), "0")))
if args[:2] == ["bundle", "validate"]:
    raise SystemExit(0)
if args[:2] == ["gate", "evaluate"]:
    bundle = args[args.index("--bundle") + 1]
    language = pathlib.Path(bundle).parent.name
    raise SystemExit(int(os.environ.get("FAKE_GATE_" + language.upper(), "0")))
raise SystemExit(64)
"""


def workflow() -> dict[str, object]:
    data = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def steps() -> list[dict[str, object]]:
    return workflow()["jobs"]["analyze"]["steps"]  # type: ignore[index]


def step(identifier: str) -> dict[str, object]:
    return next(s for s in steps() if s.get("id") == identifier)


def runner_source() -> str:
    run = str(step("instances")["run"])
    marker = "<<'PY'\n"
    start = run.index(marker) + len(marker)
    end = run.index("\nPY", start)
    return textwrap.dedent(run[start:end]) + "\n"


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.directory = self.tmp / ".l9" / "runtime" / "m" / "required-instances"
        self.directory.mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.sdk = self.bin / "l9-ci"
        self.sdk.write_text(FAKE_SDK, encoding="utf-8")
        self.sdk.chmod(self.sdk.stat().st_mode | stat.S_IXUSR)
        self.semgrep = self.bin / "semgrep"
        self.semgrep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.semgrep.chmod(self.semgrep.stat().st_mode | stat.S_IXUSR)
        self.log = self.tmp / "sdk.log"
        self.revision = json.loads(INVENTORY.read_text(encoding="utf-8"))["result"][
            "snapshot"
        ]["revision"]
        self.plan()

    def plan(self) -> None:
        shutil.copy(INVENTORY, self.directory / "inventory.json")
        completed = subprocess.run(
            [sys.executable, str(ACTIONS / "plan-required-instances" / "plan.py")],
            cwd=self.tmp,
            env={
                "PATH": os.environ["PATH"],
                "GITHUB_WORKSPACE": str(self.tmp),
                "GITHUB_OUTPUT": str(self.tmp / "plan.out"),
                "L9_PLAN_INVENTORY": ".l9/runtime/m/required-instances/inventory.json",
                "L9_PLAN_REPOSITORY": "Quantum-L9/fixture",
                "L9_PLAN_REVISION": self.revision,
                "L9_PLAN_SDK_REVISION": SDK,
                "L9_PLAN_REQUIRED_PROVIDERS": "semgrep",
                "L9_PLAN_OUTPUT": ".l9/runtime/m/required-instances/plan.json",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def run_instances(self, primary: str = "pass", expect: int = 0, **fake: str) -> str:
        env = {
            "PATH": os.environ["PATH"],
            "FAKE_LOG": str(self.log),
            "L9_INSTANCE_DIR": ".l9/runtime/m/required-instances",
            "L9_EXECUTABLE": str(self.sdk),
            "L9_PROVIDER_EXECUTABLE": str(self.semgrep),
            "L9_PRIMARY_LANGUAGE": "python",
            "L9_PRIMARY_STATUS": primary,
            "L9_STRICT": "false",
            "L9_REQUIRED": "true",
            "L9_POLICY": "policy.yaml",
            "L9_IDENTITY_DIR": "maps",
            **fake,
        }
        completed = subprocess.run(
            [sys.executable, "-I", "-c", runner_source()],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(expect, completed.returncode, completed.stderr)
        return completed.stdout + completed.stderr

    def aggregate(self) -> dict[str, str]:
        output = self.tmp / "aggregate.out"
        completed = subprocess.run(
            [
                sys.executable,
                str(ACTIONS / "aggregate-required-instances" / "aggregate.py"),
            ],
            cwd=self.tmp,
            env={
                "PATH": os.environ["PATH"],
                "GITHUB_WORKSPACE": str(self.tmp),
                "GITHUB_OUTPUT": str(output),
                "L9_INSTANCE_PLAN": ".l9/runtime/m/required-instances/plan.json",
                "L9_INSTANCE_RESULTS": ".l9/runtime/m/required-instances/results",
                "L9_INSTANCE_AGGREGATE": ".l9/runtime/m/required-instances/aggregate.json",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        pairs = (line.split("=", 1) for line in output.read_text().splitlines())
        return dict(pair for pair in pairs if len(pair) == 2)

    def test_both_languages_pass_and_the_second_one_actually_ran(self) -> None:
        printed = self.run_instances()
        self.assertIn("instance semgrep:language:python: pass", printed)
        self.assertIn("instance semgrep:language:typescript: pass", printed)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("semgrep run --language typescript", calls)
        self.assertNotIn("--language python", calls)  # primary already ran
        self.assertIn("--identity-map maps/typescript.yaml", calls)
        self.assertIn("--required", calls)
        result = self.aggregate()
        self.assertEqual(
            ("pass", "0", "true"),
            (result["status"], result["exit-code"], result["accounted"]),
        )

    def test_a_failing_second_language_fails_the_aggregate(self) -> None:
        self.run_instances(FAKE_GATE_TYPESCRIPT="1")
        result = self.aggregate()
        self.assertEqual(("fail", "1"), (result["status"], result["exit-code"]))

    def test_an_instance_that_cannot_run_is_incomplete_never_pass(self) -> None:
        printed = self.run_instances(expect=1, FAKE_RUN_TYPESCRIPT="2")
        self.assertIn(
            "::error::instance semgrep:language:typescript: SDK run failed", printed
        )
        # The primary's record is still written before the step fails.
        self.assertTrue((self.directory / "results" / "semgrep-python.json").exists())
        result = self.aggregate()
        self.assertEqual(
            ("incomplete", "6", "false"),
            (result["status"], result["exit-code"], result["accounted"]),
        )

    def test_a_primary_without_a_gate_status_is_incomplete(self) -> None:
        self.run_instances(primary="")
        result = self.aggregate()
        self.assertEqual(("incomplete", "6"), (result["status"], result["exit-code"]))

    def test_an_unexpected_gate_exit_records_nothing(self) -> None:
        printed = self.run_instances(expect=1, FAKE_GATE_TYPESCRIPT="3")
        self.assertIn("gate exited 3", printed)
        self.assertFalse(
            (self.directory / "results" / "semgrep-typescript.json").exists()
        )
        self.assertEqual("incomplete", self.aggregate()["status"])

    def test_execution_errors_fail_advisory_profiles_too(self) -> None:
        # Advisory profiles do not mark Semgrep required, so a missing optional
        # record would aggregate to pass; the step itself must fail instead.
        self.run_instances(expect=1, L9_REQUIRED="false", FAKE_RUN_TYPESCRIPT="2")

    def test_a_concluded_fail_is_a_verdict_not_an_execution_error(self) -> None:
        self.run_instances(expect=0, L9_REQUIRED="false", FAKE_GATE_TYPESCRIPT="1")

    def test_strict_profiles_pass_strict_flags(self) -> None:
        self.run_instances(L9_STRICT="true")
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("--strict", calls)
        self.assertIn("--strict-unresolved", calls)


class WiringTests(unittest.TestCase):
    def test_order_is_inventory_plan_gate_instances_aggregate_enforce(self) -> None:
        names = [s.get("id") or s.get("name") for s in steps()]
        order = [
            "language",
            "inventory",
            "plan",
            "repository_verify",
            "gate",
            "instances",
            "aggregate",
            "Enforce central mode on SDK technical gate",
        ]
        positions = [names.index(name) for name in order]
        self.assertEqual(sorted(positions), positions)

    def test_plan_and_aggregate_are_core_actions_at_the_fleet_pin(self) -> None:
        pins = {
            str(s["uses"]).split("@")[1]
            for s in steps()
            if str(s.get("uses", "")).startswith("Quantum-L9/l9-ci-core/")
        }
        self.assertEqual(1, len(pins))
        self.assertTrue(
            str(step("plan")["uses"]).startswith(
                "Quantum-L9/l9-ci-core/.github/actions/plan-required-instances@"
            )
        )
        self.assertTrue(
            str(step("aggregate")["uses"]).startswith(
                "Quantum-L9/l9-ci-core/.github/actions/aggregate-required-instances@"
            )
        )
        self.assertEqual(
            "${{ steps.gov.outputs['required-provider'] == 'true' && 'semgrep' || '' }}",
            step("plan")["with"]["required-providers"],
        )
        self.assertIn("always()", str(step("aggregate")["if"]))
        self.assertNotIn("continue-on-error", step("instances"))

    def test_enforcement_reads_the_aggregate_and_defaults_to_incomplete(self) -> None:
        enforce = next(
            s
            for s in steps()
            if s.get("name") == "Enforce central mode on SDK technical gate"
        )
        self.assertEqual(
            "${{ steps.aggregate.outputs.status }}", enforce["env"]["AGGREGATE_STATUS"]
        )
        run = str(enforce["run"])
        self.assertIn('status="${AGGREGATE_STATUS:-incomplete}"', run)
        self.assertIn('exit_code="${AGGREGATE_EXIT:-6}"', run)
        self.assertIn('exit "${exit_code}"', run)

    def test_the_check_name_is_unchanged(self) -> None:
        self.assertEqual(
            yaml.safe_load(
                (ROOT / ".l9" / "org-runtime-contract.yaml").read_text(encoding="utf-8")
            )["entrypoint"]["required_check"]["name"],
            workflow()["jobs"]["analyze"]["name"],  # type: ignore[index]
        )

    def test_the_sdk_fallback_is_the_promoted_revision(self) -> None:
        provision = next(s for s in steps() if s.get("id") == "sdk")
        self.assertIn(
            "1d63203797c692a026a9a578cf611dcf5e709cdd",
            str(provision["with"]["sdk-revision"]),
        )


if __name__ == "__main__":
    unittest.main()
