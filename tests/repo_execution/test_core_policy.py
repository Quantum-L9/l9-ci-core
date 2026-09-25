"""Core-local policy: change gates, companion rules, wiring, reporting."""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import ROOT, init_repo  # noqa: E402

from l9_repo.__main__ import (  # noqa: E402
    CORE_POLICY_PATH,
    WorkflowError,
    validate_core_policy_data,
)
from l9_repo.change_policy import (  # noqa: E402
    ChangePolicyError,
    companion_findings,
    resolve_changed_files,
    select_gates,
)
from l9_repo.contract_wiring import (  # noqa: E402
    ContractWiringError,
    validate_contract_wiring,
)
from l9_repo.reporting import StepEvidence, write_reports  # noqa: E402

POLICY_DOCUMENT = json.loads((ROOT / CORE_POLICY_PATH).read_text(encoding="utf-8"))
POLICY = POLICY_DOCUMENT["change_policy"]


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
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed with exit {result.returncode} in {root}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def seed_repo(root: pathlib.Path) -> None:
    init_repo(root)
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    run_git(root, "add", "base.txt")
    run_git(root, "commit", "-m", "base")


class CorePolicyDocumentTests(unittest.TestCase):
    def policy(self) -> dict[str, object]:
        return copy.deepcopy(POLICY_DOCUMENT)

    def test_core_policy_is_valid(self) -> None:
        data = self.policy()
        self.assertIs(validate_core_policy_data(data), data)

    def test_policy_is_not_the_consumer_contract(self) -> None:
        for consumer_key in ("commands", "push", "pull_request", "metadata"):
            self.assertNotIn(consumer_key, POLICY_DOCUMENT)

    def test_unknown_top_level_key_is_rejected(self) -> None:
        data = self.policy()
        data["surprise"] = True
        with self.assertRaisesRegex(WorkflowError, "unsupported keys"):
            validate_core_policy_data(data)

    def test_unsafe_clean_path_is_rejected(self) -> None:
        data = self.policy()
        data["clean_paths"] = ["../escape"]
        with self.assertRaisesRegex(WorkflowError, "safe relative path"):
            validate_core_policy_data(data)

    def test_unsafe_lock_name_is_rejected(self) -> None:
        data = self.policy()
        data["automation"]["lock"]["name"] = ".."  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "safe simple file name"):
            validate_core_policy_data(data)

    def test_gate_command_rejects_unallowlisted_executable(self) -> None:
        data = self.policy()
        data["change_policy"]["gates"]["workflow"]["commands"] = [  # type: ignore[index]
            ["arbitrary-script"]
        ]
        with self.assertRaisesRegex(WorkflowError, "argv-only allowlist"):
            validate_core_policy_data(data)

    def test_gate_command_rejects_shell_strings(self) -> None:
        data = self.policy()
        data["change_policy"]["gates"]["workflow"]["commands"] = [  # type: ignore[index]
            "python -m unittest"
        ]
        with self.assertRaisesRegex(WorkflowError, "argv array"):
            validate_core_policy_data(data)

    def test_companion_rule_requires_at_least_one_requirement(self) -> None:
        data = self.policy()
        rule = data["change_policy"]["companion_rules"][0]  # type: ignore[index]
        rule.pop("require_all_paths")
        with self.assertRaisesRegex(WorkflowError, "must declare"):
            validate_core_policy_data(data)

    def test_reporting_paths_cannot_escape_root(self) -> None:
        data = self.policy()
        data["reporting"]["agent_check_json"] = "../evidence.json"  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "safe relative path"):
            validate_core_policy_data(data)

    def test_authority_paths_are_safe(self) -> None:
        data = self.policy()
        data["authority"]["derived_documents"] = ["../escape.md"]  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "safe relative path"):
            validate_core_policy_data(data)


class ChangePolicyTests(unittest.TestCase):
    def test_workflow_change_selects_workflow_gate(self) -> None:
        selected = select_gates(POLICY, [".github/workflows/self-ci.yml"])
        self.assertEqual([gate.gate_id for gate in selected], ["workflow"])

    def test_repository_execution_files_select_their_gate(self) -> None:
        for path in (
            "tools/l9_repo/contract.py",
            "tools/l9_repo/facades/make-v1.mk",
            "tests/repo_execution/test_facade.py",
            ".l9/repo-workflow.json",
            ".l9/core-repo-policy.json",
            "Makefile",
            "Repo.mk",
        ):
            with self.subTest(path=path):
                selected = select_gates(POLICY, [path])
                self.assertEqual(
                    [gate.gate_id for gate in selected], ["repository-execution"]
                )
                self.assertEqual(
                    ("tests/repo_execution",),
                    tuple(
                        token
                        for token in selected[0].commands[0]
                        if token.startswith("tests/")
                    ),
                )

    def test_multiple_gates_preserve_declared_order(self) -> None:
        selected = select_gates(
            POLICY,
            ["tools/l9_repo/__main__.py", ".github/actions/x/action.yml"],
        )
        self.assertEqual(
            [gate.gate_id for gate in selected], ["actions", "repository-execution"]
        )

    def test_workflow_change_requires_test_and_manifest(self) -> None:
        findings = companion_findings(POLICY, [".github/workflows/self-ci.yml"])
        self.assertEqual(
            [finding.rule_id for finding in findings],
            ["manifest-integrity", "workflow-tests"],
        )

    def test_workflow_companions_satisfy_rules(self) -> None:
        findings = companion_findings(
            POLICY,
            [
                ".github/workflows/self-ci.yml",
                "tests/workflows/test_self_ci.py",
                "MANIFEST.sha256",
            ],
        )
        self.assertEqual(findings, [])

    def test_sdk_pin_requires_every_documented_mirror(self) -> None:
        files = [
            ".l9/sdk-compatibility.yaml",
            "tests/provisioning/test_compatibility_manifest.py",
            "MANIFEST.sha256",
        ]
        findings = companion_findings(POLICY, files)
        sdk = next(item for item in findings if item.rule_id == "sdk-pin-mirrors")
        self.assertIn(".github/actions/provision-sdk/provision.py", sdk.missing_all)
        self.assertIn(".github/workflows/publish-analysis.yml", sdk.missing_all)

    def test_repository_execution_requires_suite_docs_agents_and_manifest(
        self,
    ) -> None:
        findings = companion_findings(POLICY, ["tools/l9_repo/__main__.py"])
        rule = next(
            item for item in findings if item.rule_id == "repository-execution-evidence"
        )
        self.assertEqual(rule.required_any, ("tests/repo_execution/",))
        self.assertEqual(
            set(rule.missing_all),
            {"AGENTS.md", "docs/repository-execution-runtime.md", "MANIFEST.sha256"},
        )

    def test_explicit_files_are_deduplicated_and_sorted(self) -> None:
        resolution = resolve_changed_files(ROOT, explicit=["b", "a", "b"])
        self.assertEqual(resolution.files, ("a", "b"))
        self.assertEqual(resolution.source, "explicit")

    def test_explicit_paths_must_be_canonical_repository_relative(self) -> None:
        for path in ("../escape", "/absolute", "./relative", "windows\\path"):
            with self.subTest(path=path):
                with self.assertRaises(ChangePolicyError):
                    resolve_changed_files(ROOT, explicit=[path])

    def test_rename_exposes_old_and_new_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            seed_repo(root)
            old = root / ".github/workflows/old.yml"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_text("name: old\n", encoding="utf-8")
            run_git(root, "add", ".")
            run_git(root, "commit", "-m", "old")
            run_git(root, "branch", "base")
            run_git(root, "checkout", "-b", "feature")
            new = root / "docs/new.yml"
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            run_git(root, "add", "-A")
            run_git(root, "commit", "-m", "rename")
            resolution = resolve_changed_files(root, base_ref="base")
            self.assertIn(".github/workflows/old.yml", resolution.files)
            self.assertIn("docs/new.yml", resolution.files)

    def test_clean_feature_branch_uses_merge_base_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            seed_repo(root)
            run_git(root, "checkout", "-b", "feature")
            (root / "feature.txt").write_text("feature\n", encoding="utf-8")
            run_git(root, "add", "feature.txt")
            run_git(root, "commit", "-m", "feature")
            resolution = resolve_changed_files(root, base_ref="main")
            self.assertEqual(resolution.files, ("feature.txt",))
            self.assertEqual(resolution.base_ref, "main")

    def test_working_tree_and_committed_changes_are_unioned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            seed_repo(root)
            run_git(root, "checkout", "-b", "feature")
            (root / "committed.txt").write_text("committed\n", encoding="utf-8")
            run_git(root, "add", "committed.txt")
            run_git(root, "commit", "-m", "committed")
            (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            resolution = resolve_changed_files(root, base_ref="main")
            self.assertEqual(resolution.files, ("committed.txt", "untracked.txt"))

    def test_no_context_on_clean_repo_is_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            seed_repo(root)
            with self.assertRaisesRegex(ChangePolicyError, "no changed-file context"):
                resolve_changed_files(root)

    def test_unavailable_base_is_error_when_tree_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            seed_repo(root)
            with self.assertRaisesRegex(ChangePolicyError, "comparison ref"):
                resolve_changed_files(root, base_ref="origin/missing")

    def test_unavailable_base_uses_working_tree_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            seed_repo(root)
            (root / "working.txt").write_text("working\n", encoding="utf-8")
            resolution = resolve_changed_files(root, base_ref="origin/missing")
            self.assertEqual(resolution.files, ("working.txt",))
            self.assertIn("comparison-unavailable", resolution.source)

    def test_git_execution_failure_is_usage_error(self) -> None:
        with mock.patch(
            "l9_repo.change_policy.subprocess.run",
            side_effect=FileNotFoundError("git missing"),
        ):
            with self.assertRaisesRegex(ChangePolicyError, "unable to execute git"):
                resolve_changed_files(ROOT, base_ref="main")


class ContractWiringTests(unittest.TestCase):
    def test_exact_markdown_link_or_code_reference_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / ".l9").mkdir()
            (root / ".l9/ownership.yaml").write_text("x\n", encoding="utf-8")
            (root / "AGENTS.md").write_text(
                "Read [ownership](.l9/ownership.yaml).\n", encoding="utf-8"
            )
            spec = {
                "required_files": ["AGENTS.md", ".l9/ownership.yaml"],
                "reference_requirements": [
                    {
                        "target": ".l9/ownership.yaml",
                        "instruction_files": ["AGENTS.md"],
                    }
                ],
            }
            validate_contract_wiring(root, spec)

    def test_filename_substring_in_unrelated_prose_does_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / ".l9").mkdir()
            (root / ".l9/ownership.yaml").write_text("x\n", encoding="utf-8")
            (root / "AGENTS.md").write_text(
                "The file .l9/ownership.yaml exists somewhere.\n", encoding="utf-8"
            )
            spec = {
                "required_files": ["AGENTS.md", ".l9/ownership.yaml"],
                "reference_requirements": [
                    {
                        "target": ".l9/ownership.yaml",
                        "instruction_files": ["AGENTS.md"],
                    }
                ],
            }
            with self.assertRaisesRegex(ContractWiringError, "not referenced exactly"):
                validate_contract_wiring(root, spec)

    def test_missing_authoritative_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "AGENTS.md").write_text("`missing.yaml`\n", encoding="utf-8")
            spec = {
                "required_files": ["AGENTS.md", "missing.yaml"],
                "reference_requirements": [
                    {"target": "missing.yaml", "instruction_files": ["AGENTS.md"]}
                ],
            }
            with self.assertRaisesRegex(ContractWiringError, "missing authoritative"):
                validate_contract_wiring(root, spec)


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
            for _ in range(2):
                write_reports(
                    json_path,
                    md_path,
                    files=["a.py"],
                    change_source="comparison+working-tree",
                    base_ref="origin/main",
                    head_ref="HEAD",
                    findings=[],
                    steps=steps,
                    overall_exit_code=1,
                    subject_sha="abc123",
                    policy_sha256="f" * 64,
                )
                if _ == 0:
                    first_json = json_path.read_text()
                    first_md = md_path.read_text()
            self.assertEqual(json_path.read_text(), first_json)
            self.assertEqual(md_path.read_text(), first_md)
            payload = json.loads(first_json)
            self.assertEqual(payload["overall_exit_code"], 1)
            self.assertEqual(payload["steps"][0]["stderr"], "err\n")
            self.assertIn("## Step Results", first_md)
            self.assertIn("Evidence: lint:1", first_md)

    def test_reports_redact_secrets_and_escape_embedded_markup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            json_path = root / "evidence.json"
            md_path = root / "evidence.md"
            token = "ghp_" + "A" * 30
            steps = [
                StepEvidence(
                    "x",
                    ("tool",),
                    1,
                    "finding",
                    True,
                    stdout=f"Authorization: Bearer {token}\n```boom",
                )
            ]
            write_reports(
                json_path,
                md_path,
                files=["a"],
                change_source="explicit",
                base_ref=None,
                head_ref=None,
                findings=[],
                steps=steps,
                overall_exit_code=1,
                subject_sha="abc",
                policy_sha256="d" * 64,
            )
            payload = json.loads(json_path.read_text())
            self.assertNotIn(token, payload["steps"][0]["stdout"])
            markdown = md_path.read_text()
            self.assertNotIn(token, markdown)
            self.assertIn("<pre>", markdown)
            self.assertIn("```boom", markdown)


if __name__ == "__main__":
    unittest.main()
