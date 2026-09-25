"""Repository Execution V2 admission bridge: dual-read V1 + V2 verification.

``run-repository-verification`` must admit both contract generations while
Core itself stays a V1 consumer:

- a ``schema_version == 1`` contract still delegates to the unchanged
  ``RepositoryWorkflow`` V1 runtime;
- the exact three-field ``l9.repo-execution/v2`` contract runs the minimal
  ``make setup -> validate -> check -> test`` bridge from the supplied
  workspace and fails closed on any deviation;
- anything else is an invalid contract, never guessed as V1 or V2.

The final section holds tripwires proving this bridge did not become the V2
adoption change: Core's own contract remains V1 and the bridge dispatches no
publication or Governance command.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
CORE_CONTRACT = ROOT / ".l9" / "repo-workflow.json"

sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.contract import validate_v2_contract_data  # noqa: E402

V2_PHASES = ["setup", "validate", "check", "test"]


def _load_runner():
    spec = importlib.util.spec_from_file_location("l9_repository_verify_bridge", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def v2_contract(**overrides: object) -> dict[str, object]:
    contract: dict[str, object] = {
        "schema": "l9.repo-execution/v2",
        "facade": "make-v1",
        "required_phases": list(V2_PHASES),
    }
    contract.update(overrides)
    return contract


def write_contract(root: Path, document: object) -> None:
    (root / ".l9").mkdir(exist_ok=True)
    text = document if isinstance(document, str) else json.dumps(document)
    (root / ".l9" / "repo-workflow.json").write_text(text + "\n", encoding="utf-8")


def write_makefile(root: Path, *, failing_phase: str | None = None) -> None:
    """A minimal make-v1 facade that logs each phase it runs into phases.log."""

    lines = [".PHONY: " + " ".join(V2_PHASES), ""]
    for phase in V2_PHASES:
        lines.append(f"{phase}:")
        lines.append(f"\t@printf '{phase}\\n' >> phases.log")
        if phase == failing_phase:
            lines.append("\t@exit 1")
        lines.append("")
    (root / "Makefile").write_text("\n".join(lines), encoding="utf-8")


class RefusingWorkflow:
    """Stand-in proving the V1 runtime is never touched on the V2 path."""

    def __init__(self, workspace: Path) -> None:
        raise AssertionError("RepositoryWorkflow must not be constructed here")


def run_verifier(
    runner, workspace: Path, *, workflow_class: object | None = None
) -> dict[str, str]:
    """Run ``main()`` against ``workspace`` and return the parsed outputs."""

    outputs: dict[str, str] = {}
    with tempfile.NamedTemporaryFile() as output:
        env = {"L9_REPOSITORY_WORKSPACE": str(workspace), "GITHUB_OUTPUT": output.name}
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            if workflow_class is None:
                code = runner.main()
            else:
                with unittest.mock.patch.object(
                    runner, "RepositoryWorkflow", workflow_class
                ):
                    code = runner.main()
        assert code == 0
        for line in Path(output.name).read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs


class ContractClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()

    def test_exact_three_field_v2_contract_is_recognized(self) -> None:
        self.assertEqual("v2", self.runner.classify_contract(v2_contract()))

    def test_existing_v1_contract_is_recognized(self) -> None:
        self.assertEqual("v1", self.runner.classify_contract({"schema_version": 1}))

    def test_core_contract_is_admitted_as_v2_by_bridge_and_runtime(self) -> None:
        document = json.loads(CORE_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual("v2", self.runner.classify_contract(document))
        validate_v2_contract_data(document)

    def test_v2_rejections_fail_closed(self) -> None:
        cases: dict[str, object] = {
            "unknown field": v2_contract(commands={}),
            "missing schema": {"facade": "make-v1", "required_phases": V2_PHASES},
            "missing facade": {
                "schema": "l9.repo-execution/v2",
                "required_phases": V2_PHASES,
            },
            "missing phases": {"schema": "l9.repo-execution/v2", "facade": "make-v1"},
            "wrong facade": v2_contract(facade="make-v2"),
            "missing phase": v2_contract(
                required_phases=["setup", "validate", "check"]
            ),
            "extra phase": v2_contract(required_phases=[*V2_PHASES, "lint"]),
            "duplicate phase": v2_contract(required_phases=[*V2_PHASES, "test"]),
            "reordered phases": v2_contract(
                required_phases=["setup", "check", "validate", "test"]
            ),
            "phases not a list": v2_contract(
                required_phases="setup validate check test"
            ),
            "v1 marker inside v2": v2_contract(schema_version=1),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(self.runner.ContractError):
                    self.runner.classify_contract(document)

    def test_unknown_generation_is_never_guessed(self) -> None:
        cases: dict[str, object] = {
            "empty object": {},
            "wrong schema string": {
                "schema": "l9.repo-execution/v3",
                "facade": "make-v1",
                "required_phases": V2_PHASES,
            },
            "wrong schema_version": {"schema_version": 2},
            "boolean schema_version": {"schema_version": True},
            "string schema_version": {"schema_version": "1"},
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(self.runner.ContractError):
                    self.runner.classify_contract(document)

    def test_non_object_or_unparseable_document_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in {"array": "[]", "broken": "{"}.items():
                with self.subTest(case=name):
                    write_contract(root, text)
                    with self.assertRaises(self.runner.ContractError):
                        self.runner.load_contract(root / ".l9" / "repo-workflow.json")


class V1CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()

    def test_v1_contract_uses_v1_runtime_and_not_the_bridge(self) -> None:
        calls: list[str] = []

        class FakeWorkflow:
            def __init__(self, workspace: Path) -> None:
                calls.append(f"workspace:{workspace}")

            def setup(self) -> None:
                calls.append("setup")

            def validate(self) -> None:
                calls.append("validate")

            def check(self) -> None:
                calls.append("check")

            def test(self) -> None:
                calls.append("test")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            write_contract(root, {"schema_version": 1})
            with unittest.mock.patch.object(
                self.runner.subprocess, "run", side_effect=AssertionError
            ):
                out = run_verifier(self.runner, root, workflow_class=FakeWorkflow)
        self.assertEqual(
            [f"workspace:{root}", "setup", "validate", "check", "test"], calls
        )
        self.assertEqual({"present": "true", "status": "pass"}, out)

    def test_v1_execution_failure_remains_fail(self) -> None:
        class FailingWorkflow:
            def __init__(self, workspace: Path) -> None:
                pass

            def setup(self) -> None:
                pass

            def validate(self) -> None:
                raise RuntimeError("expected V1 failure")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, {"schema_version": 1})
            out = run_verifier(self.runner, root, workflow_class=FailingWorkflow)
        self.assertEqual({"present": "true", "status": "fail"}, out)


class V2ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()

    def test_v2_runs_make_phases_in_order_from_the_supplied_workspace(self) -> None:
        recorded: list[tuple[list[str], Path, bool]] = []

        def fake_run(argv, *, cwd, check):
            recorded.append((list(argv), Path(cwd), check))
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            write_contract(root, v2_contract())
            with unittest.mock.patch.object(self.runner.subprocess, "run", fake_run):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(
            [(["make", phase], root, True) for phase in V2_PHASES], recorded
        )
        self.assertEqual({"present": "true", "status": "pass"}, out)

    def test_v2_stops_at_first_failing_phase_and_records_fail(self) -> None:
        recorded: list[str] = []

        def fake_run(argv, *, cwd, check):
            recorded.append(argv[1])
            if argv[1] == "check":
                raise subprocess.CalledProcessError(2, argv)
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, v2_contract())
            with unittest.mock.patch.object(self.runner.subprocess, "run", fake_run):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(["setup", "validate", "check"], recorded)
        self.assertEqual({"present": "true", "status": "fail"}, out)

    def test_malformed_v2_contract_never_reaches_the_v1_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, v2_contract(commands={}))
            with unittest.mock.patch.object(
                self.runner.subprocess, "run", side_effect=AssertionError
            ):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual({"present": "true", "status": "fail"}, out)

    def test_unknown_generation_fails_closed_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, {"schema_version": 2})
            with unittest.mock.patch.object(
                self.runner.subprocess, "run", side_effect=AssertionError
            ):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual({"present": "true", "status": "fail"}, out)

    def test_absent_contract_is_still_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = run_verifier(self.runner, Path(tmp), workflow_class=RefusingWorkflow)
        self.assertEqual({"present": "false", "status": "not_applicable"}, out)


@unittest.skipUnless(
    shutil.which("make"),
    "GNU make is not installed; the mocked V2ExecutionTests still cover ordering",
)
class V2RealMakeTests(unittest.TestCase):
    """End-to-end proof through a real ``make`` against a throwaway consumer."""

    def setUp(self) -> None:
        self.runner = _load_runner()

    def _poison_consumer_runtime(self, root: Path) -> Path:
        """Plant consumer-owned Core-shaped modules that must never be imported."""

        marker = root / "consumer-runtime-imported"
        package = root / "tools" / "l9_repo"
        package.mkdir(parents=True)
        poison = (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('imported')\n"
            "raise RuntimeError('consumer runtime imported as authority')\n"
        )
        (root / "tools" / "__init__.py").write_text(poison, encoding="utf-8")
        (package / "__init__.py").write_text(poison, encoding="utf-8")
        (package / "__main__.py").write_text(poison, encoding="utf-8")
        return marker

    def test_successful_v2_consumer_executes_all_phases_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            write_contract(root, v2_contract())
            write_makefile(root)
            marker = self._poison_consumer_runtime(root)
            modules_before = set(sys.modules)
            out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
            self.assertEqual({"present": "true", "status": "pass"}, out)
            self.assertEqual(
                "setup\nvalidate\ncheck\ntest\n",
                (root / "phases.log").read_text(encoding="utf-8"),
            )
            self.assertFalse(marker.exists(), "consumer runtime was imported")
            self.assertNotIn(str(root), sys.path)
            for name in set(sys.modules) - modules_before:
                module_file = getattr(sys.modules[name], "__file__", None) or ""
                self.assertFalse(
                    module_file.startswith(str(root)),
                    f"{name} was imported from the consumer workspace",
                )

    def test_failing_v2_phase_stops_the_run_and_records_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            write_contract(root, v2_contract())
            write_makefile(root, failing_phase="validate")
            out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
            self.assertEqual({"present": "true", "status": "fail"}, out)
            self.assertEqual(
                "setup\nvalidate\n",
                (root / "phases.log").read_text(encoding="utf-8"),
            )


class BridgeScopeTripwires(unittest.TestCase):
    """PR A builds the bridge; it must not cross it."""

    def test_core_itself_is_a_v2_consumer_of_the_bridge(self) -> None:
        """PR A built the bridge; the V2 adoption crossed it. Both sides hold."""

        document = json.loads(CORE_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual("l9.repo-execution/v2", document.get("schema"))
        self.assertNotIn("schema_version", document)
        self.assertEqual({"schema", "facade", "required_phases"}, set(document))

    def test_bridge_dispatches_no_publication_or_governance_command(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        for token in ("l9 pr", "git push", "gh pr", "release", "deploy"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_bridge_executes_only_the_declared_make_v1_phases(self) -> None:
        runner = _load_runner()
        self.assertEqual(tuple(V2_PHASES), runner.V2_REQUIRED_PHASES)
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn('argv = ["make", phase]', text)
        self.assertNotIn("shell=True", text)
        self.assertNotIn("l9_make", text)

    def test_bridge_keeps_the_existing_action_output_contract(self) -> None:
        text = (RUNNER.parent / "action.yml").read_text(encoding="utf-8")
        outputs = text.split("outputs:", 1)[1].split("runs:", 1)[0]
        declared = {
            line.strip().rstrip(":")
            for line in outputs.splitlines()
            if line.startswith("  ") and not line.startswith("    ")
        }
        self.assertEqual({"present", "status"}, declared)


if __name__ == "__main__":
    unittest.main()
