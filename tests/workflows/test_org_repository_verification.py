"""Contract tests for central repository verification in the org runtime."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "org-ci.yml"
ACTION = ROOT / ".github" / "actions" / "run-repository-verification" / "action.yml"
RUNNER = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
PIN = "63428872c2fc010f79d319f499b9ce844b181063"


def _load_runner():
    spec = importlib.util.spec_from_file_location("l9_repository_verify_action", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def v2() -> str:
    return json.dumps(
        {
            "schema": "l9.repo-execution/v2",
            "facade": "make-v1",
            "required_phases": ["setup", "validate", "check", "test"],
        }
    )


class OrgRepositoryVerificationTests(unittest.TestCase):
    def test_org_workflow_uses_immutable_core_action_pin(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            f"Quantum-L9/l9-ci-core/.github/actions/run-repository-verification@{PIN}",
            text,
        )
        self.assertNotIn("run-repository-verification@main", text)

    def test_runner_uses_pinned_core_runtime_not_consumer_runtime_module(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("CORE_ROOT = Path(__file__).resolve().parents[3]", text)
        self.assertIn('CORE_TOOLS = CORE_ROOT / "tools"', text)
        self.assertIn("from l9_repo.__main__", text)
        self.assertNotIn('workspace / "tools" / "l9_repo"', text)

    def test_migration_mode_absent_contract_is_typed_legacy_not_applicable(
        self,
    ) -> None:
        runner = _load_runner()
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            with unittest.mock.patch.dict(
                os.environ,
                {"L9_REPOSITORY_WORKSPACE": tmp, "GITHUB_OUTPUT": output.name},
                clear=False,
            ):
                self.assertEqual(0, runner.main())
            text = Path(output.name).read_text(encoding="utf-8")
            self.assertIn("status=legacy_not_applicable", text)
            self.assertIn("contract-version=absent", text)

    def test_required_mode_absent_contract_fails_closed(self) -> None:
        runner = _load_runner()
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            with unittest.mock.patch.dict(
                os.environ,
                {
                    "L9_REPOSITORY_WORKSPACE": tmp,
                    "GITHUB_OUTPUT": output.name,
                    "L9_REPOSITORY_CONTRACT_MODE": "required",
                },
                clear=False,
            ):
                self.assertEqual(0, runner.main())
            text = Path(output.name).read_text(encoding="utf-8")
            self.assertIn("status=missing_repository_contract", text)
            self.assertIn("failure-kind=contract", text)

    def test_v2_executes_make_abi_in_exact_order(self) -> None:
        runner = _load_runner()
        calls: list[str] = []

        class FakeWorkflow:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def contract_version(self) -> str:
                return "v2"

            def verify_generated(self) -> None:
                calls.append("verify-generated")

            def execute_phase(self, phase: str) -> None:
                calls.append(phase)

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            root = Path(tmp)
            (root / ".l9").mkdir()
            (root / ".l9/repo-workflow.json").write_text(v2(), encoding="utf-8")
            with (
                unittest.mock.patch.dict(
                    os.environ,
                    {"L9_REPOSITORY_WORKSPACE": tmp, "GITHUB_OUTPUT": output.name},
                    clear=False,
                ),
                unittest.mock.patch.object(runner, "RepositoryWorkflow", FakeWorkflow),
            ):
                self.assertEqual(0, runner.main())
            self.assertEqual(
                ["verify-generated", "setup", "validate", "check", "test"], calls
            )
            self.assertIn("status=pass", Path(output.name).read_text(encoding="utf-8"))

    def test_v1_compatibility_is_bounded_and_distinct(self) -> None:
        runner = _load_runner()
        calls: list[str] = []

        class FakeWorkflow:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def contract_version(self) -> str:
                return "v1"

            def execute_legacy_phase(self, phase: str) -> None:
                calls.append(phase)

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            root = Path(tmp)
            (root / ".l9").mkdir()
            (root / ".l9/repo-workflow.json").write_text(
                '{"schema_version": 1}', encoding="utf-8"
            )
            with (
                unittest.mock.patch.dict(
                    os.environ,
                    {"L9_REPOSITORY_WORKSPACE": tmp, "GITHUB_OUTPUT": output.name},
                    clear=False,
                ),
                unittest.mock.patch.object(runner, "RepositoryWorkflow", FakeWorkflow),
            ):
                self.assertEqual(0, runner.main())
            self.assertEqual(["setup", "validate", "check", "test"], calls)
            text = Path(output.name).read_text(encoding="utf-8")
            self.assertIn("contract-version=v1", text)

    def test_malformed_contract_is_a_typed_contract_failure(self) -> None:
        runner = _load_runner()
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            root = Path(tmp)
            (root / ".l9").mkdir()
            (root / ".l9/repo-workflow.json").write_text("{}", encoding="utf-8")
            with unittest.mock.patch.dict(
                os.environ,
                {"L9_REPOSITORY_WORKSPACE": tmp, "GITHUB_OUTPUT": output.name},
                clear=False,
            ):
                self.assertEqual(0, runner.main())
            self.assertIn(
                "status=contract_failure", Path(output.name).read_text(encoding="utf-8")
            )

    def test_composite_action_exposes_typed_v2_outputs(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        for name in (
            "present:",
            "status:",
            "contract-version:",
            "failure-kind:",
            "contract-mode:",
        ):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
