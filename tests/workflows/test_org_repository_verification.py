"""Contract tests for central repository verification in the org runtime."""

from __future__ import annotations

import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class OrgRepositoryVerificationTests(unittest.TestCase):
    def test_org_workflow_uses_immutable_core_action_pin(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        expected = (
            f"Quantum-L9/l9-ci-core/.github/actions/run-repository-verification@{PIN}"
        )
        self.assertIn(expected, text)
        self.assertRegex(PIN, re.compile(r"^[0-9a-f]{40}$"))
        self.assertNotIn("run-repository-verification@main", text)

    def test_repository_verification_result_channel_preserves_semgrep(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        verify = text.index("id: repository_verify")
        semgrep = text.index("name: Install Semgrep (exact central pin)")
        self.assertLess(verify, semgrep)
        self.assertNotIn("continue-on-error", text[verify:semgrep])
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn('_write_output("status", "fail")', runner)

    def test_blocking_profiles_enforce_repository_verification(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        enforce = text.index("name: Enforce repository verification")
        tail = text[enforce:]
        self.assertIn("steps.runtime.outputs.profile == 'pr_fast'", tail)
        self.assertIn("steps.runtime.outputs.profile == 'merge'", tail)
        self.assertIn("steps.repository_verify.outputs.status", tail)

    def test_summary_reports_repository_verification(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Repository verification:", text)
        self.assertIn("steps.repository_verify.outputs.status", text)

    def test_action_uses_pinned_core_runtime_not_consumer_runtime_module(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("CORE_ROOT = Path(__file__).resolve().parents[3]", text)
        self.assertIn('CORE_TOOLS = CORE_ROOT / "tools"', text)
        self.assertIn("from l9_repo.__main__ import RepositoryWorkflow", text)
        self.assertNotIn('workspace / "tools" / "l9_repo"', text)

    def test_absent_contract_is_not_applicable(self) -> None:
        runner = _load_runner()
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            env = {
                "L9_REPOSITORY_WORKSPACE": tmp,
                "GITHUB_OUTPUT": output.name,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(0, runner.main())
            text = Path(output.name).read_text(encoding="utf-8")
            self.assertIn("present=false", text)
            self.assertIn("status=not_applicable", text)

    def test_present_contract_runs_setup_validate_check_test_in_order(self) -> None:
        runner = _load_runner()
        calls: list[str] = []

        class FakeWorkflow:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def setup(self) -> None:
                calls.append("setup")

            def validate(self) -> None:
                calls.append("validate")

            def check(self) -> None:
                calls.append("check")

            def test(self) -> None:
                calls.append("test")

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            root = Path(tmp)
            (root / ".l9").mkdir()
            (root / ".l9" / "repo-workflow.json").write_text("{}\n", encoding="utf-8")
            env = {
                "L9_REPOSITORY_WORKSPACE": tmp,
                "GITHUB_OUTPUT": output.name,
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch.object(runner, "RepositoryWorkflow", FakeWorkflow),
            ):
                self.assertEqual(0, runner.main())
            self.assertEqual(["setup", "validate", "check", "test"], calls)
            text = Path(output.name).read_text(encoding="utf-8")
            self.assertIn("present=true", text)
            self.assertIn("status=pass", text)

    def test_failed_contract_emits_typed_failure_for_later_enforcement(self) -> None:
        runner = _load_runner()

        class FailingWorkflow:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def setup(self) -> None:
                raise RuntimeError("expected repository failure")

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as output,
        ):
            root = Path(tmp)
            (root / ".l9").mkdir()
            (root / ".l9" / "repo-workflow.json").write_text("{}\n", encoding="utf-8")
            env = {
                "L9_REPOSITORY_WORKSPACE": tmp,
                "GITHUB_OUTPUT": output.name,
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch.object(runner, "RepositoryWorkflow", FailingWorkflow),
            ):
                self.assertEqual(0, runner.main())
            text = Path(output.name).read_text(encoding="utf-8")
            self.assertIn("present=true", text)
            self.assertIn("status=fail", text)

    def test_composite_action_exposes_presence_and_status(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        self.assertIn("present:", text)
        self.assertIn("status:", text)
        self.assertIn("github.action_path", text)


if __name__ == "__main__":
    unittest.main()
