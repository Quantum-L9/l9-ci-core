"""Repository Execution V2 admission bridge: dual-read V1 + V2 verification.

``run-repository-verification`` must admit both contract generations; Core
itself is admitted through the V2 branch:

- a ``schema_version == 1`` contract still delegates to the unchanged
  ``RepositoryWorkflow`` V1 runtime;
- the exact three-field ``l9.repo-execution/v2`` contract is admitted only
  with a byte-exact generated ``make-v1`` facade and a ``Repo.mk`` that
  implements the ABI, then runs ``make setup -> validate -> check -> test``
  from the supplied workspace and fails closed on any deviation;
- anything else is an invalid contract, never guessed as V1 or V2;
- whether a contract must be present is Core's bundled enforcement policy
  (``migration`` or ``required``), keyed by the runner-supplied repository,
  never something the repository declares;
- every result is typed: ``contract-mode``, ``contract-version`` and
  ``failure-kind`` distinguish an invalid contract from a failed command.

The final section holds tripwires: Core's own contract is the exact V2
declaration, Core's own tree passes the bridge's V2 admission, and the bridge
dispatches no publication or Governance command.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
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

from l9_repo.__main__ import WorkflowError  # noqa: E402
from l9_repo.contract import render_facade, validate_v2_contract_data  # noqa: E402

V2_PHASES = ["setup", "validate", "check", "test"]
POLICY = RUNNER.parent / "contract-policy.json"


def typed(
    status: str,
    *,
    present: bool = True,
    mode: str = "migration",
    version: str = "v2",
    failure: str = "none",
) -> dict[str, str]:
    return {
        "present": str(present).lower(),
        "status": status,
        "contract-mode": mode,
        "contract-version": version,
        "failure-kind": failure,
    }


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


def write_v2_consumer(root: Path, *, failing_phase: str | None = None) -> None:
    """The released make-v1 facade plus a Repo.mk logging each phase it runs."""

    (root / "Makefile").write_bytes(render_facade("make-v1"))
    leaves = [f"repo-{phase}" for phase in V2_PHASES]
    lines = [".PHONY: " + " ".join(leaves), ""]
    for phase in V2_PHASES:
        lines.append(f"repo-{phase}:")
        lines.append(f"\t@printf '{phase}\\n' >> phases.log")
        if phase == failing_phase:
            lines.append("\t@exit 1")
        lines.append("")
    (root / "Repo.mk").write_text("\n".join(lines), encoding="utf-8")


class RefusingWorkflow:
    """Stand-in proving the V1 runtime is never touched on the V2 path."""

    def __init__(self, workspace: Path) -> None:
        raise AssertionError("RepositoryWorkflow must not be constructed here")


def run_verifier(
    runner,
    workspace: Path,
    *,
    workflow_class: object | None = None,
    repository: str = "Quantum-L9/example-consumer",
) -> dict[str, str]:
    """Run ``main()`` against ``workspace`` and return the parsed outputs."""

    outputs: dict[str, str] = {}
    with tempfile.NamedTemporaryFile() as output:
        env = {
            "L9_REPOSITORY_WORKSPACE": str(workspace),
            "GITHUB_OUTPUT": output.name,
            "L9_REPOSITORY_IDENTITY": repository,
        }
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
        self.assertEqual(typed("pass", version="v1"), out)

    def test_v1_execution_failure_remains_fail(self) -> None:
        class FailingWorkflow:
            def __init__(self, workspace: Path) -> None:
                pass

            def setup(self) -> None:
                pass

            def validate(self) -> None:
                raise subprocess.CalledProcessError(1, ["ruff", "check", "."])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, {"schema_version": 1})
            out = run_verifier(self.runner, root, workflow_class=FailingWorkflow)
        self.assertEqual(typed("fail", version="v1", failure="command"), out)

    def test_invalid_v1_document_is_a_contract_failure(self) -> None:
        class InvalidWorkflow:
            def __init__(self, workspace: Path) -> None:
                pass

            def setup(self) -> None:
                raise WorkflowError("invalid V1 repository contract")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, {"schema_version": 1})
            out = run_verifier(self.runner, root, workflow_class=InvalidWorkflow)
        self.assertEqual(typed("fail", version="v1", failure="contract"), out)


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
            write_v2_consumer(root)
            with unittest.mock.patch.object(self.runner.subprocess, "run", fake_run):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(
            [(["make", phase], root, True) for phase in V2_PHASES], recorded
        )
        self.assertEqual(typed("pass"), out)

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
            write_v2_consumer(root)
            with unittest.mock.patch.object(self.runner.subprocess, "run", fake_run):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(["setup", "validate", "check"], recorded)
        self.assertEqual(typed("fail", failure="command"), out)

    def test_malformed_v2_contract_never_reaches_the_v1_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, v2_contract(commands={}))
            with unittest.mock.patch.object(
                self.runner.subprocess, "run", side_effect=AssertionError
            ):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(typed("fail", version="invalid", failure="contract"), out)

    def test_unknown_generation_fails_closed_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, {"schema_version": 2})
            with unittest.mock.patch.object(
                self.runner.subprocess, "run", side_effect=AssertionError
            ):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(typed("fail", version="invalid", failure="contract"), out)

    def test_absent_contract_is_still_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = run_verifier(self.runner, Path(tmp), workflow_class=RefusingWorkflow)
        self.assertEqual(typed("not_applicable", present=False, version="absent"), out)

    def _assert_rejected_before_execution(self, mutate, expected_message: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, v2_contract())
            write_v2_consumer(root)
            mutate(root)
            stderr = io.StringIO()
            with (
                unittest.mock.patch.object(
                    self.runner.subprocess, "run", side_effect=AssertionError
                ),
                contextlib.redirect_stderr(stderr),
            ):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(typed("fail", failure="contract"), out)
        self.assertIn(expected_message, stderr.getvalue())

    def test_altered_generated_facade_fails_before_any_phase_runs(self) -> None:
        def alter(root: Path) -> None:
            facade = (root / "Makefile").read_text(encoding="utf-8")
            (root / "Makefile").write_text(
                facade.replace("test: repo-test", "test:"), encoding="utf-8"
            )

        self._assert_rejected_before_execution(alter, "drift from facade template")

    def test_missing_generated_facade_fails_before_any_phase_runs(self) -> None:
        self._assert_rejected_before_execution(
            lambda root: (root / "Makefile").unlink(), "missing generated Makefile"
        )

    def test_missing_repo_mk_phase_fails_before_any_phase_runs(self) -> None:
        def drop_test_leaf(root: Path) -> None:
            text = (root / "Repo.mk").read_text(encoding="utf-8")
            (root / "Repo.mk").write_text(
                text.replace("repo-test:", "repo-tests:"), encoding="utf-8"
            )

        self._assert_rejected_before_execution(
            drop_test_leaf, "missing required implementation target: repo-test"
        )

    def test_missing_repo_mk_fails_before_any_phase_runs(self) -> None:
        self._assert_rejected_before_execution(
            lambda root: (root / "Repo.mk").unlink(),
            "missing repository implementation: Repo.mk",
        )

    def test_repo_mk_cannot_redefine_a_facade_phase(self) -> None:
        def hijack(root: Path) -> None:
            with (root / "Repo.mk").open("a", encoding="utf-8") as stream:
                stream.write("\ntest:\n\t@true\n")

        self._assert_rejected_before_execution(hijack, "redefines facade target")

    def test_conditionally_declared_phase_counts_as_missing(self) -> None:
        # GNU make treats a .PHONY target without a rule as satisfied, so a
        # leaf hidden behind a false conditional would pass without running.
        def hide(wrapper: tuple[str, str]):
            opener, closer = wrapper

            def mutate(root: Path) -> None:
                text = (root / "Repo.mk").read_text(encoding="utf-8")
                leaf = "repo-test:\n\t@printf 'test\\n' >> phases.log\n"
                self.assertIn(leaf, text)
                (root / "Repo.mk").write_text(
                    text.replace(leaf, f"{opener}\n{leaf}{closer}\n"),
                    encoding="utf-8",
                )

            return mutate

        for wrapper in (
            ("ifdef NEVER", "endif"),
            ("ifeq ($(CI),never)", "else\nendif"),
            ("define HIDDEN", "endef"),
        ):
            with self.subTest(wrapper=wrapper[0]):
                self._assert_rejected_before_execution(
                    hide(wrapper),
                    "missing required implementation target: repo-test",
                )

    def test_conditional_phony_does_not_satisfy_the_abi(self) -> None:
        def hide_phony(root: Path) -> None:
            text = (root / "Repo.mk").read_text(encoding="utf-8")
            first, rest = text.split("\n", 1)
            self.assertTrue(first.startswith(".PHONY: "))
            (root / "Repo.mk").write_text(
                f"ifdef NEVER\n{first}\nendif\n{rest}", encoding="utf-8"
            )

        self._assert_rejected_before_execution(hide_phony, "must be declared .PHONY")

    def test_missing_make_is_an_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, v2_contract())
            write_v2_consumer(root)
            with unittest.mock.patch.object(
                self.runner.subprocess,
                "run",
                side_effect=FileNotFoundError("make"),
            ):
                out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
        self.assertEqual(typed("fail", failure="infrastructure"), out)


class EnforcementPolicyTests(unittest.TestCase):
    """Contract presence is Core policy, resolved from runner identity only."""

    def setUp(self) -> None:
        self.runner = _load_runner()

    def _policy(self, **overrides: object) -> dict[str, object]:
        policy: dict[str, object] = {
            "schema": "l9.repository-contract-enforcement/v1",
            "default_mode": "migration",
            "required_repositories": ["Quantum-L9/enrolled"],
        }
        policy.update(overrides)
        return policy

    def _run(self, root: Path, policy: object, repository: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as policy_dir:
            path = Path(policy_dir) / "contract-policy.json"
            text = policy if isinstance(policy, str) else json.dumps(policy)
            path.write_text(text, encoding="utf-8")
            with unittest.mock.patch.object(self.runner, "POLICY_PATH", path):
                return run_verifier(
                    self.runner,
                    root,
                    workflow_class=RefusingWorkflow,
                    repository=repository,
                )

    def test_bundled_policy_enrolls_no_repository_in_required_mode(self) -> None:
        policy = self.runner.load_policy()
        self.assertEqual("migration", policy["default_mode"])
        self.assertEqual([], policy["required_repositories"])

    def test_required_mode_fails_an_absent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(Path(tmp), self._policy(), "Quantum-L9/enrolled")
        self.assertEqual(
            typed(
                "fail",
                present=False,
                mode="required",
                version="absent",
                failure="contract",
            ),
            out,
        )

    def test_required_mode_matches_repository_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(Path(tmp), self._policy(), "quantum-l9/ENROLLED")
        self.assertEqual("required", out["contract-mode"])
        self.assertEqual("fail", out["status"])

    def test_unenrolled_repository_keeps_migration_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(Path(tmp), self._policy(), "Quantum-L9/other")
        self.assertEqual(typed("not_applicable", present=False, version="absent"), out)

    def test_required_mode_rejects_v1_without_executing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, {"schema_version": 1})
            with unittest.mock.patch.object(
                self.runner.subprocess, "run", side_effect=AssertionError
            ):
                out = self._run(root, self._policy(), "Quantum-L9/enrolled")
        self.assertEqual(
            typed("fail", mode="required", version="v1", failure="contract"), out
        )

    def test_required_mode_admits_a_valid_v2_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, v2_contract())
            write_v2_consumer(root)
            with unittest.mock.patch.object(
                self.runner.subprocess,
                "run",
                side_effect=lambda argv, **_: subprocess.CompletedProcess(argv, 0),
            ):
                out = self._run(root, self._policy(), "Quantum-L9/enrolled")
        self.assertEqual(typed("pass", mode="required"), out)

    def test_repository_declarations_cannot_select_a_weaker_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_contract(root, {"schema_version": 1, "contract_mode": "migration"})
            out = self._run(root, self._policy(), "Quantum-L9/enrolled")
        self.assertEqual("required", out["contract-mode"])
        self.assertEqual("fail", out["status"])
        action = (RUNNER.parent / "action.yml").read_text(encoding="utf-8")
        inputs = action.split("inputs:", 1)[1].split("outputs:", 1)[0]
        self.assertNotIn("mode", inputs)
        self.assertIn("L9_REPOSITORY_IDENTITY: ${{ github.repository }}", action)

    def test_inadmissible_policy_fails_closed_as_infrastructure(self) -> None:
        cases: dict[str, object] = {
            "unparseable": "{",
            "unknown key": self._policy(optional_repositories=[]),
            "wrong schema": self._policy(schema="l9.other/v1"),
            "unsupported mode": self._policy(default_mode="advisory"),
            "bad repository": self._policy(required_repositories=["enrolled"]),
            "duplicate repository": self._policy(
                required_repositories=["Quantum-L9/a", "quantum-l9/A"]
            ),
        }
        for name, policy in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                out = self._run(Path(tmp), policy, "Quantum-L9/other")
                self.assertEqual("fail", out["status"])
                self.assertEqual("infrastructure", out["failure-kind"])
                self.assertEqual("unresolved", out["contract-mode"])


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
            write_v2_consumer(root)
            marker = self._poison_consumer_runtime(root)
            modules_before = set(sys.modules)
            out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
            self.assertEqual(typed("pass"), out)
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
            write_v2_consumer(root, failing_phase="validate")
            out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
            self.assertEqual(typed("fail", failure="command"), out)
            self.assertEqual(
                "setup\nvalidate\n",
                (root / "phases.log").read_text(encoding="utf-8"),
            )

    def test_deliberately_failing_repository_test_fails_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            write_contract(root, v2_contract())
            write_v2_consumer(root, failing_phase="test")
            out = run_verifier(self.runner, root, workflow_class=RefusingWorkflow)
            self.assertEqual(typed("fail", failure="command"), out)
            self.assertEqual(
                "setup\nvalidate\ncheck\ntest\n",
                (root / "phases.log").read_text(encoding="utf-8"),
            )


class BridgeScopeTripwires(unittest.TestCase):
    """The bridge admits Core as V2 and stays an execution-only admission point."""

    def test_core_itself_is_a_v2_consumer_of_the_bridge(self) -> None:
        document = json.loads(CORE_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual("l9.repo-execution/v2", document.get("schema"))
        self.assertNotIn("schema_version", document)
        self.assertEqual({"schema", "facade", "required_phases"}, set(document))

    def test_core_tree_passes_the_bridge_v2_admission(self) -> None:
        # The facade bytes and Repo.mk ABI the bridge checks before any phase.
        runner = _load_runner()
        runner.verify_v2(ROOT)

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

    def test_bridge_declares_exactly_the_typed_action_outputs(self) -> None:
        text = (RUNNER.parent / "action.yml").read_text(encoding="utf-8")
        outputs = text.split("outputs:", 1)[1].split("runs:", 1)[0]
        declared = {
            line.strip().rstrip(":")
            for line in outputs.splitlines()
            if line.startswith("  ") and not line.startswith("    ")
        }
        self.assertEqual(
            {"present", "status", "contract-mode", "contract-version", "failure-kind"},
            declared,
        )

    def test_bridge_has_one_v2_contract_authority(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("from l9_repo.contract import", text)
        self.assertNotIn("V2_ALLOWED_KEYS", text)
        self.assertNotIn("def validate_v2_contract", text)


if __name__ == "__main__":
    unittest.main()
