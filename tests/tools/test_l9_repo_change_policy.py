from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.change_policy import (  # noqa: E402
    ChangePolicyError,
    companion_findings,
    resolve_changed_files,
    select_gates,
)
from l9_repo.contract_wiring import ContractWiringError, validate_contract_wiring  # noqa: E402
from l9_repo.reporting import StepEvidence, write_reports  # noqa: E402

POLICY = json.loads((ROOT / ".l9/core-repo-policy.json").read_text())["change_policy"]


def run_git(root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def init_repo(root: pathlib.Path) -> None:
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.email", "tests@example.com")
    run_git(root, "config", "user.name", "Tests")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    run_git(root, "add", "base.txt")
    run_git(root, "commit", "-m", "base")


class ChangePolicyTests(unittest.TestCase):
    def test_repository_execution_changes_select_core_local_gate(self) -> None:
        selected = select_gates(POLICY, ["tools/l9_repo/__main__.py"])
        self.assertEqual([gate.gate_id for gate in selected], ["repository-execution"])

    def test_workflow_change_requires_test_and_manifest(self) -> None:
        findings = companion_findings(POLICY, [".github/workflows/self-ci.yml"])
        self.assertEqual(
            [finding.rule_id for finding in findings],
            ["manifest-integrity", "workflow-tests"],
        )

    def test_repository_execution_requires_tests_docs_agents_and_manifest(self) -> None:
        findings = companion_findings(POLICY, ["tools/l9_repo/__main__.py"])
        execution = next(
            item for item in findings if item.rule_id == "repository-execution-evidence"
        )
        self.assertEqual(execution.required_any, ("tests/tools/",))
        self.assertEqual(
            set(execution.missing_all),
            {"AGENTS.md", "docs/repository-execution-runtime.md", "MANIFEST.sha256"},
        )

    def test_explicit_files_are_deduplicated_and_sorted(self) -> None:
        resolution = resolve_changed_files(ROOT, explicit=["b", "a", "b"])
        self.assertEqual(resolution.files, ("a", "b"))

    def test_explicit_paths_must_be_canonical_repository_relative(self) -> None:
        for path in ("../escape", "/absolute", "./relative", "windows\\path"):
            with self.subTest(path=path):
                with self.assertRaises(ChangePolicyError):
                    resolve_changed_files(ROOT, explicit=[path])

    def test_clean_feature_branch_uses_merge_base_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            init_repo(root)
            run_git(root, "checkout", "-b", "feature")
            (root / "feature.txt").write_text("feature\n", encoding="utf-8")
            run_git(root, "add", "feature.txt")
            run_git(root, "commit", "-m", "feature")
            resolution = resolve_changed_files(root, base_ref="main")
            self.assertEqual(resolution.files, ("feature.txt",))

    def test_unavailable_base_uses_working_tree_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            init_repo(root)
            (root / "working.txt").write_text("working\n", encoding="utf-8")
            resolution = resolve_changed_files(root, base_ref="origin/missing")
            self.assertEqual(resolution.files, ("working.txt",))
            self.assertIn("comparison-unavailable", resolution.source)


class ContractWiringTests(unittest.TestCase):
    def test_exact_markdown_link_or_code_reference_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / ".l9").mkdir()
            (root / ".l9/ownership.yaml").write_text("x\n", encoding="utf-8")
            (root / "AGENTS.md").write_text(
                "Read [ownership](.l9/ownership.yaml).\n", encoding="utf-8"
            )
            validate_contract_wiring(
                root,
                {
                    "required_files": ["AGENTS.md", ".l9/ownership.yaml"],
                    "reference_requirements": [
                        {
                            "target": ".l9/ownership.yaml",
                            "instruction_files": ["AGENTS.md"],
                        }
                    ],
                },
            )

    def test_missing_authoritative_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "AGENTS.md").write_text("`missing.yaml`\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractWiringError, "missing authoritative"):
                validate_contract_wiring(
                    root,
                    {
                        "required_files": ["AGENTS.md", "missing.yaml"],
                        "reference_requirements": [
                            {
                                "target": "missing.yaml",
                                "instruction_files": ["AGENTS.md"],
                            }
                        ],
                    },
                )


class ReportingTests(unittest.TestCase):
    def test_json_and_markdown_reports_are_deterministic_and_evidence_bearing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            json_path = root / "evidence.json"
            md_path = root / "evidence.md"
            steps = [
                StepEvidence(
                    "lint:1",
                    ("ruff", "check", "."),
                    1,
                    "finding",
                    True,
                    stdout="out\n",
                    stderr="err\n",
                )
            ]
            kwargs = dict(
                files=["a.py"],
                change_source="comparison",
                base_ref="origin/main",
                head_ref="HEAD",
                findings=[],
                steps=steps,
                overall_exit_code=1,
                subject_sha="abc123",
                policy_sha256="f" * 64,
            )
            write_reports(json_path, md_path, **kwargs)
            first_json, first_md = json_path.read_text(), md_path.read_text()
            write_reports(json_path, md_path, **kwargs)
            self.assertEqual(json_path.read_text(), first_json)
            self.assertEqual(md_path.read_text(), first_md)
            self.assertIn("## Step Results", first_md)

    def test_reports_redact_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            token = "ghp_" + "A" * 30
            json_path, md_path = root / "evidence.json", root / "evidence.md"
            write_reports(
                json_path,
                md_path,
                files=["a"],
                change_source="explicit",
                base_ref=None,
                head_ref=None,
                findings=[],
                steps=[
                    StepEvidence(
                        "x", ("tool",), 1, "finding", True, stdout=f"Bearer {token}"
                    )
                ],
                overall_exit_code=1,
                subject_sha="abc",
                policy_sha256="d" * 64,
            )
            self.assertNotIn(token, json_path.read_text())


if __name__ == "__main__":
    unittest.main()
