from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo import locking, push_preflight  # noqa: E402
from l9_repo.authority import AuthorityError, validate_authority  # noqa: E402
from l9_repo.__main__ import (  # noqa: E402
    AgentCheckFailure,
    RepositoryWorkflow,
    WorkflowError,
    main,
    validate_config_data,
)


def run_git(
    root: pathlib.Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=check,
    )


def initialize_target_fixture(root: pathlib.Path) -> None:
    for relative in (
        ".l9/architecture.yaml",
        ".l9/ownership.yaml",
        ".l9/sdk-compatibility.yaml",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("schema: test\n", encoding="utf-8")
    (root / "AGENTS.md").write_text(
        "\n".join(
            [
                "[architecture](.l9/architecture.yaml)",
                "[ownership](.l9/ownership.yaml)",
                "[compatibility](.l9/sdk-compatibility.yaml)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    checker = root / "tools/check_workflow_integrity.py"
    checker.parent.mkdir(parents=True, exist_ok=True)
    checker.write_text("raise SystemExit(0)\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        "artifacts/\n__pycache__/\n*.pyc\n", encoding="utf-8"
    )
    (root / "requirements-ci.txt").write_text(
        "# target-owned fixture\n", encoding="utf-8"
    )


def regenerate_manifest(root: pathlib.Path) -> None:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "MANIFEST.sha256" or relative.startswith(
            (".git/", "artifacts/")
        ):
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files.append(relative)
    lines = [
        f"{hashlib.sha256((root / relative).read_bytes()).hexdigest()}  {relative}"
        for relative in sorted(files)
    ]
    (root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def simple_command(exit_code: int = 0, output: str = "") -> list[str]:
    code = f"print({output!r}); raise SystemExit({exit_code})"
    return ["@python", "-c", code]


def configure_simple_commands(root: pathlib.Path) -> dict[str, object]:
    path = root / ".l9/repo-workflow.json"
    config = json.loads(path.read_text())
    config["commands"] = {
        "setup": [simple_command()],
        "validate": [simple_command()],
        "check": [simple_command()],
        "test": [simple_command()],
    }
    for gate in config["change_policy"]["gates"].values():
        gate["commands"] = [simple_command()]
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    regenerate_manifest(root)
    return config


def make_git_fixture() -> tuple[tempfile.TemporaryDirectory[str], pathlib.Path]:
    temporary = tempfile.TemporaryDirectory()
    root = pathlib.Path(temporary.name)
    shutil.copytree(
        ROOT,
        root,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "artifacts"),
    )
    initialize_target_fixture(root)
    configure_simple_commands(root)
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.email", "tests@example.com")
    run_git(root, "config", "user.name", "Tests")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "base")
    run_git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    run_git(root, "checkout", "-b", "feature")
    return temporary, root


class ConfigTests(unittest.TestCase):
    def load_config(self) -> dict[str, object]:
        return json.loads((ROOT / ".l9/repo-workflow.json").read_text())

    def test_repository_config_is_valid(self) -> None:
        data = self.load_config()
        self.assertIs(validate_config_data(data), data)

    def test_authority_metadata_is_canonical(self) -> None:
        data = self.load_config()
        data["metadata"]["contract_status"] = "draft"  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "must be authoritative"):
            validate_config_data(data)

    def test_authority_paths_are_safe(self) -> None:
        data = self.load_config()
        data["authority"]["derived_documents"] = ["../escape.md"]  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "safe relative path"):
            validate_config_data(data)

    def test_unknown_top_level_key_is_rejected(self) -> None:
        data = self.load_config()
        data["surprise"] = True
        with self.assertRaisesRegex(WorkflowError, "unsupported keys"):
            validate_config_data(data)

    def test_safety_flags_cannot_be_disabled(self) -> None:
        data = self.load_config()
        data["push"]["reject_protected_branch"] = False  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "must be true"):
            validate_config_data(data)

    def test_empty_command_matrix_is_rejected(self) -> None:
        data = self.load_config()
        data["commands"]["test"] = []  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "non-empty"):
            validate_config_data(data)

    def test_shell_string_is_rejected(self) -> None:
        data = self.load_config()
        data["commands"]["check"] = ["ruff check ."]  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "argv array"):
            validate_config_data(data)

    def test_unsafe_clean_path_is_rejected(self) -> None:
        data = self.load_config()
        data["clean_paths"] = ["../escape"]
        with self.assertRaisesRegex(WorkflowError, "safe relative path"):
            validate_config_data(data)

    def test_unsafe_lock_name_is_rejected(self) -> None:
        data = self.load_config()
        data["automation"]["lock"]["name"] = ".."  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "safe simple file name"):
            validate_config_data(data)

    def test_pull_request_base_must_be_protected(self) -> None:
        data = self.load_config()
        data["pull_request"]["base"] = "develop"  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "configured protected branch"):
            validate_config_data(data)

    def test_companion_rule_requires_at_least_one_requirement(self) -> None:
        data = self.load_config()
        rule = data["change_policy"]["companion_rules"][0]  # type: ignore[index]
        rule.pop("require_all_paths")
        with self.assertRaisesRegex(WorkflowError, "must declare"):
            validate_config_data(data)

    def test_reporting_paths_cannot_escape_root(self) -> None:
        data = self.load_config()
        data["reporting"]["agent_check_json"] = "../evidence.json"  # type: ignore[index]
        with self.assertRaisesRegex(WorkflowError, "safe relative path"):
            validate_config_data(data)


class AuthorityIntegrationTests(unittest.TestCase):
    def test_target_repository_root_docs_do_not_need_component_identity(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        (root / "README.md").write_text("# l9-ci-core\n", encoding="utf-8")
        for pack_only in (
            "AUTHORITY.md",
            "MANIFEST.md",
            "OPERATIONS.md",
            "VALIDATION.md",
        ):
            path = root / pack_only
            if path.exists():
                path.unlink()
        config = json.loads((root / ".l9/repo-workflow.json").read_text())
        validate_authority(root, config)

    def test_derived_runtime_doc_must_declare_identity_and_version(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        config = json.loads((root / ".l9/repo-workflow.json").read_text())
        derived = root / config["authority"]["derived_documents"][0]
        derived.write_text("# Runtime\n", encoding="utf-8")
        with self.assertRaisesRegex(AuthorityError, "authoritative token"):
            validate_authority(root, config)

    def test_all_target_authorities_are_required(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        config = json.loads((root / ".l9/repo-workflow.json").read_text())
        (root / ".l9/ownership.yaml").unlink()
        with self.assertRaisesRegex(AuthorityError, "missing target authority"):
            validate_authority(root, config)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = RepositoryWorkflow(ROOT)

    def test_makefile_matches_template(self) -> None:
        self.assertEqual(
            (ROOT / "Makefile").read_bytes(),
            (ROOT / "tools/l9_repo/Makefile.template").read_bytes(),
        )

    def test_python_sentinel_uses_running_interpreter(self) -> None:
        self.assertEqual(
            self.workflow.render_argv(["@python", "-m", "unittest"])[0],
            sys.executable,
        )

    def test_workspace_inside_repo_is_rejected(self) -> None:
        completed = subprocess.CompletedProcess(["git"], 0, str(ROOT) + "\n", "")
        workflow = RepositoryWorkflow(ROOT / "tests")
        with mock.patch.object(workflow, "git", return_value=completed):
            with self.assertRaisesRegex(WorkflowError, "not repository root"):
                workflow._ensure_repository_root()

    def test_non_git_workspace_is_taxonomy_two(self) -> None:
        result = subprocess.CompletedProcess(["git"], 128, "", "not a repo")
        with mock.patch.object(self.workflow, "git", return_value=result):
            with self.assertRaisesRegex(WorkflowError, "not a repo"):
                self.workflow._ensure_repository_root()

    def test_validate_runs_structural_and_configured_validation(self) -> None:
        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "structural_validate") as structural,
            mock.patch.object(self.workflow, "invoke") as invoke,
        ):
            self.workflow.validate()
        structural.assert_called_once_with()
        invoke.assert_called_once_with("validate")

    def test_cli_validate_reaches_validator(self) -> None:
        with mock.patch.object(RepositoryWorkflow, "validate") as validate:
            self.assertEqual(main(["--workspace", str(ROOT), "validate"]), 0)
        validate.assert_called_once_with()

    def test_change_policy_cli_missing_context_exits_two(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        run_git(root, "checkout", "main")
        run_git(root, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(
            main(["--workspace", str(root), "change-policy"]),
            2,
        )

    def test_agent_check_collects_multiple_findings_and_writes_both_reports(
        self,
    ) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        config_path = root / ".l9/repo-workflow.json"
        config = json.loads(config_path.read_text())
        config["commands"]["validate"] = [simple_command(1, "validate failed")]
        config["commands"]["check"] = [simple_command(1, "check failed")]
        config["commands"]["test"] = [simple_command(0, "test passed")]
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        regenerate_manifest(root)
        run_git(root, "add", ".l9/repo-workflow.json", "MANIFEST.sha256")
        run_git(root, "commit", "-m", "configure failures")
        workflow = RepositoryWorkflow(root)
        with self.assertRaisesRegex(AgentCheckFailure, "2 blocking finding"):
            workflow.agent_check(explicit=["MANIFEST.sha256"])
        payload = json.loads((root / "artifacts/agent-check-evidence.json").read_text())
        self.assertEqual(payload["overall_exit_code"], 1)
        self.assertEqual(
            [step["classification"] for step in payload["steps"] if step["command"]],
            ["finding", "finding", "pass"],
        )
        self.assertTrue((root / "artifacts/agent-check-evidence.md").is_file())

    def test_missing_executable_is_infrastructure_exit_two_after_other_steps(
        self,
    ) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        config_path = root / ".l9/repo-workflow.json"
        config = json.loads(config_path.read_text())
        config["commands"]["validate"] = [["definitely-not-a-real-tool"]]
        config["commands"]["check"] = [simple_command(0, "check still ran")]
        config["commands"]["test"] = [simple_command(0, "test still ran")]
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        regenerate_manifest(root)
        run_git(root, "add", ".l9/repo-workflow.json", "MANIFEST.sha256")
        run_git(root, "commit", "-m", "configure infra failure")
        workflow = RepositoryWorkflow(root)
        with self.assertRaisesRegex(WorkflowError, "infrastructure"):
            workflow.agent_check(explicit=["MANIFEST.sha256"])
        payload = json.loads((root / "artifacts/agent-check-evidence.json").read_text())
        self.assertEqual(payload["overall_exit_code"], 2)
        classes = [step["classification"] for step in payload["steps"]]
        self.assertIn("infrastructure", classes)
        self.assertGreaterEqual(classes.count("pass"), 2)

    def test_agent_check_uses_committed_feature_diff_on_clean_tree(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        workflow_path = root / ".github/workflows/example.yml"
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text("name: example\n", encoding="utf-8")
        test_path = root / "tests/workflows/test_example.py"
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text("# companion\n", encoding="utf-8")
        regenerate_manifest(root)
        run_git(root, "add", ".")
        run_git(root, "commit", "-m", "workflow change")
        workflow = RepositoryWorkflow(root)
        workflow.agent_check()
        payload = json.loads((root / "artifacts/agent-check-evidence.json").read_text())
        self.assertIn(".github/workflows/example.yml", payload["changed_files"])
        names = [step["name"] for step in payload["steps"]]
        self.assertTrue(any(name.startswith("change-gate:workflow") for name in names))

    def test_agent_check_does_not_dirty_tracked_worktree(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        workflow = RepositoryWorkflow(root)
        workflow.agent_check(explicit=["MANIFEST.sha256"])
        self.assertEqual(run_git(root, "status", "--porcelain").stdout.strip(), "")

    def test_protected_branch_is_refused_before_fetch(self) -> None:
        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "branch", return_value="main"),
            mock.patch.object(self.workflow, "git") as git,
        ):
            with self.assertRaisesRegex(WorkflowError, "protected branch"):
                self.workflow._push_unlocked()
        git.assert_not_called()

    def test_detached_head_is_refused_before_fetch(self) -> None:
        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "branch", return_value=""),
            mock.patch.object(self.workflow, "git") as git,
        ):
            with self.assertRaisesRegex(WorkflowError, "detached HEAD"):
                self.workflow._push_unlocked()
        git.assert_not_called()

    def test_remote_ahead_refuses_when_rebase_disabled(self) -> None:
        config = self.workflow.config()
        completed = subprocess.CompletedProcess(["git"], 0, "", "")
        remote = subprocess.CompletedProcess(["git"], 0, "remote\n", "")
        behind = subprocess.CompletedProcess(["git"], 0, "1 2\n", "")

        def fake_git(*args: str, **_: object) -> subprocess.CompletedProcess[str]:
            if args[:2] == ("rev-parse", "--verify"):
                return remote
            if args[:3] == ("rev-list", "--left-right", "--count"):
                return behind
            return completed

        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "config", return_value=config),
            mock.patch.object(
                self.workflow, "_assert_push_state", return_value="feature"
            ),
            mock.patch.object(self.workflow, "git", side_effect=fake_git),
        ):
            with self.assertRaisesRegex(WorkflowError, "remote branch has commits"):
                self.workflow._push_unlocked()

    def test_rebase_happens_before_agent_check(self) -> None:
        config = self.workflow.config()
        config["push"]["rebase_before_push"] = True
        completed = subprocess.CompletedProcess(["git"], 0, "", "")
        remote = subprocess.CompletedProcess(["git"], 0, "remote\n", "")
        behind = subprocess.CompletedProcess(["git"], 0, "1 2\n", "")
        no_upstream = subprocess.CompletedProcess(["git"], 1, "", "")
        head = subprocess.CompletedProcess(["git"], 0, "abc123\n", "")
        events: list[str] = []

        def fake_git(*args: str, **_: object) -> subprocess.CompletedProcess[str]:
            if args[:2] == ("rev-parse", "--verify"):
                return remote
            if args[:3] == ("rev-list", "--left-right", "--count"):
                return behind
            if args[:3] == ("rev-parse", "--abbrev-ref", "--symbolic-full-name"):
                return no_upstream
            if args == ("rev-parse", "HEAD"):
                return head
            if args and args[0] == "rebase":
                events.append("rebase")
            return completed

        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "config", return_value=config),
            mock.patch.object(
                self.workflow, "_assert_push_state", return_value="feature"
            ),
            mock.patch.object(self.workflow, "git", side_effect=fake_git),
            mock.patch.object(
                self.workflow,
                "_run_push_gates",
                side_effect=lambda _: events.append("gates"),
            ),
        ):
            self.workflow._push_unlocked()
        self.assertEqual(events, ["rebase", "gates"])

    def test_status_reports_live_divergence(self) -> None:
        config = self.workflow.config()

        def fake_git(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args == ("branch", "--show-current"):
                return subprocess.CompletedProcess(["git"], 0, "feature\n", "")
            if args == ("rev-parse", "HEAD"):
                return subprocess.CompletedProcess(["git"], 0, "abc\n", "")
            if args[:2] == ("fetch", "--prune"):
                return subprocess.CompletedProcess(["git"], 0, "", "")
            if args[:2] == ("rev-parse", "--verify"):
                ref = args[2]
                code = 0 if ref == "origin/feature" else 1
                return subprocess.CompletedProcess(
                    ["git"], code, "ok\n" if code == 0 else "", ""
                )
            if args[:3] == ("rev-list", "--left-right", "--count"):
                return subprocess.CompletedProcess(["git"], 0, "2 3\n", "")
            if args == ("status", "--porcelain"):
                return subprocess.CompletedProcess(["git"], 0, "", "")
            raise AssertionError(args)

        output = io.StringIO()
        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "config", return_value=config),
            mock.patch.object(self.workflow, "git", side_effect=fake_git),
            mock.patch.object(
                self.workflow,
                "run",
                return_value=subprocess.CompletedProcess(["gh"], 1, "", ""),
            ),
            contextlib.redirect_stdout(output),
        ):
            self.workflow.status()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["remote_freshness"], "fresh")
        self.assertEqual(payload["behind"], 2)
        self.assertEqual(payload["ahead"], 3)
        self.assertEqual(payload["comparison_source"], "live")

    def test_status_offline_uses_cached_counts_without_claiming_freshness(self) -> None:
        config = self.workflow.config()

        def fake_git(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args == ("branch", "--show-current"):
                return subprocess.CompletedProcess(["git"], 0, "feature\n", "")
            if args == ("rev-parse", "HEAD"):
                return subprocess.CompletedProcess(["git"], 0, "abc\n", "")
            if args[:2] == ("fetch", "--prune"):
                return subprocess.CompletedProcess(["git"], 1, "", "offline")
            if args[:2] == ("rev-parse", "--verify"):
                return subprocess.CompletedProcess(["git"], 0, "cached\n", "")
            if args[:3] == ("rev-list", "--left-right", "--count"):
                return subprocess.CompletedProcess(["git"], 0, "0 1\n", "")
            if args == ("status", "--porcelain"):
                return subprocess.CompletedProcess(["git"], 0, "", "")
            raise AssertionError(args)

        output = io.StringIO()
        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "config", return_value=config),
            mock.patch.object(self.workflow, "git", side_effect=fake_git),
            mock.patch.object(
                self.workflow,
                "run",
                return_value=subprocess.CompletedProcess(["gh"], 1, "", ""),
            ),
            contextlib.redirect_stdout(output),
        ):
            self.workflow.status()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["remote_freshness"], "unknown_offline")
        self.assertEqual(payload["comparison_source"], "cached")
        self.assertEqual(payload["fetch_error"], "offline")

    def test_agent_check_fails_if_a_gate_mutates_tracked_content(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        config_path = root / ".l9/repo-workflow.json"
        config = json.loads(config_path.read_text())
        config["commands"]["check"] = [
            [
                "@python",
                "-c",
                "from pathlib import Path; Path('README.md').write_text('mutated\\n')",
            ]
        ]
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        regenerate_manifest(root)
        run_git(root, "add", ".l9/repo-workflow.json", "MANIFEST.sha256")
        run_git(root, "commit", "-m", "mutating gate")
        with self.assertRaisesRegex(WorkflowError, "infrastructure"):
            RepositoryWorkflow(root).agent_check(explicit=["MANIFEST.sha256"])
        payload = json.loads((root / "artifacts/agent-check-evidence.json").read_text())
        check = next(
            step for step in payload["steps"] if step["name"] == "non-mutation-check"
        )
        self.assertEqual(check["classification"], "infrastructure")
        self.assertIn("worktree content changed", check["stderr"])

    def test_push_refuses_when_validation_dirties_worktree(self) -> None:
        config = self.workflow.config()
        completed = subprocess.CompletedProcess(["git"], 0, "", "")
        missing_remote = subprocess.CompletedProcess(["git"], 1, "", "")
        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "config", return_value=config),
            mock.patch.object(
                self.workflow, "_assert_push_state", return_value="feature"
            ),
            mock.patch.object(self.workflow, "_run_push_gates"),
            mock.patch.object(
                self.workflow, "status_porcelain", return_value=" M generated.txt"
            ),
            mock.patch.object(
                self.workflow, "git", side_effect=[completed, missing_remote]
            ),
        ):
            with self.assertRaisesRegex(
                WorkflowError, "validation changed the worktree"
            ):
                self.workflow._push_unlocked()

    def test_structural_validation_applies_json_schema(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        schema_path = root / ".l9/repo-workflow.schema.json"
        schema = json.loads(schema_path.read_text())
        schema["required"].append("schema_only_required_key")
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        regenerate_manifest(root)
        with self.assertRaisesRegex(WorkflowError, "schema validation failed"):
            RepositoryWorkflow(root).structural_validate()

    def test_checksum_manifest_mismatch_is_rejected(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        (root / "README.md").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowError, "checksum mismatch"):
            RepositoryWorkflow(root).structural_validate()

    def test_status_without_any_remote_ref_reports_unavailable(self) -> None:
        config = self.workflow.config()

        def fake_git(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args == ("branch", "--show-current"):
                return subprocess.CompletedProcess(["git"], 0, "feature\n", "")
            if args == ("rev-parse", "HEAD"):
                return subprocess.CompletedProcess(["git"], 0, "abc\n", "")
            if args[:2] == ("fetch", "--prune"):
                return subprocess.CompletedProcess(["git"], 1, "", "offline")
            if args[:2] == ("rev-parse", "--verify"):
                return subprocess.CompletedProcess(["git"], 1, "", "")
            if args == ("status", "--porcelain"):
                return subprocess.CompletedProcess(["git"], 0, "", "")
            raise AssertionError(args)

        output = io.StringIO()
        with (
            mock.patch.object(self.workflow, "_ensure_repository_root"),
            mock.patch.object(self.workflow, "config", return_value=config),
            mock.patch.object(self.workflow, "git", side_effect=fake_git),
            mock.patch.object(
                self.workflow,
                "run",
                return_value=subprocess.CompletedProcess(["gh"], 1, "", ""),
            ),
            contextlib.redirect_stdout(output),
        ):
            self.workflow.status()
        payload = json.loads(output.getvalue())
        self.assertIsNone(payload["comparison_ref"])
        self.assertEqual(payload["comparison_source"], "unavailable")

    def test_checksum_manifest_rejects_symlinked_entry(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        outside = pathlib.Path(temporary.name).parent / "outside-l9-test.txt"
        outside.write_text("outside\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = root / "linked.txt"
        link.symlink_to(outside)
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        with (root / "MANIFEST.sha256").open("a", encoding="utf-8") as handle:
            handle.write(f"{digest}  linked.txt\n")
        with self.assertRaisesRegex(WorkflowError, "symlinked"):
            RepositoryWorkflow(root).structural_validate()

    def test_clean_never_escapes_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            workflow = RepositoryWorkflow(root)
            with (
                mock.patch.object(workflow, "_ensure_repository_root"),
                mock.patch.object(
                    workflow, "config", return_value={"clean_paths": ["../x"]}
                ),
            ):
                with self.assertRaisesRegex(WorkflowError, "unsafe clean path"):
                    workflow.clean()


class PrimitiveTests(unittest.TestCase):
    def test_unmerged_paths_block(self) -> None:
        result = mock.Mock(stdout="bad.py\n", returncode=0)
        with mock.patch.object(push_preflight, "run", return_value=result):
            with self.assertRaises(push_preflight.PreflightError):
                push_preflight.verify_no_unmerged(pathlib.Path("."))

    def test_lockfile_failure_blocks(self) -> None:
        result = mock.Mock(stdout="", stderr="stale", returncode=1)
        with mock.patch.object(push_preflight, "run", return_value=result):
            with self.assertRaises(push_preflight.PreflightError):
                push_preflight.verify_lockfile(
                    pathlib.Path("."), ["uv", "lock", "--check"]
                )

    def test_lock_is_single_flight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "operation.lock"
            with locking.single_flight(path):
                with self.assertRaises(locking.LockBusy):
                    with locking.single_flight(path):
                        pass
            self.assertFalse(path.exists())

    def test_stale_lock_with_owner_marker_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "operation.lock"
            path.mkdir()
            (path / "owner").write_text("999999999\n", encoding="utf-8")
            os.utime(path, (0, 0))
            with mock.patch.object(locking.time, "time", return_value=10_000):
                with locking.single_flight(path, stale_after=1):
                    self.assertTrue(path.exists())
            self.assertFalse(path.exists())

    def test_stale_lock_is_kept_when_owner_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "operation.lock"
            path.mkdir()
            (path / "owner").write_text("123\n", encoding="utf-8")
            os.utime(path, (0, 0))
            with (
                mock.patch.object(locking.time, "time", return_value=10_000),
                mock.patch.object(locking.os, "kill", return_value=None),
            ):
                with self.assertRaisesRegex(locking.LockBusy, "still running"):
                    with locking.single_flight(path, stale_after=1):
                        pass

    def test_stale_lock_with_unexpected_content_is_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "operation.lock"
            path.mkdir()
            (path / "unexpected").write_text("x\n", encoding="utf-8")
            os.utime(path, (0, 0))
            with mock.patch.object(locking.time, "time", return_value=10_000):
                with self.assertRaisesRegex(locking.LockBusy, "not safely removable"):
                    with locking.single_flight(path, stale_after=1):
                        pass
            self.assertTrue((path / "unexpected").is_file())

    def test_main_reports_workflow_error_as_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(
                RepositoryWorkflow, "doctor", side_effect=WorkflowError("broken")
            ),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main(["--workspace", str(ROOT), "doctor"]), 2)
        self.assertIn("broken", stderr.getvalue())

    def test_no_shell_execution_primitives_in_engine(self) -> None:
        text = (ROOT / "tools/l9_repo/__main__.py").read_text()
        change_text = (ROOT / "tools/l9_repo/change_policy.py").read_text()
        combined = text + change_text
        self.assertNotIn("shell=True", combined)
        self.assertNotIn("os.system", combined)
        self.assertNotIn("shlex", combined)
        self.assertNotIn("eval(", combined)

    def test_structural_validation_rejects_identity_document_version_drift(
        self,
    ) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        derived = root / "docs/repository-execution-runtime.md"
        derived.write_text(
            derived.read_text(encoding="utf-8").replace("4.3.1", "9.9.9"),
            encoding="utf-8",
        )
        regenerate_manifest(root)
        with self.assertRaisesRegex(WorkflowError, "authoritative token"):
            RepositoryWorkflow(root).structural_validate()

    def test_structural_validation_rejects_missing_dependency_manifest(self) -> None:
        temporary, root = make_git_fixture()
        self.addCleanup(temporary.cleanup)
        (root / "requirements-repo-runtime.txt").unlink()
        regenerate_manifest(root)
        with self.assertRaisesRegex(
            WorkflowError, "missing component dependency manifest"
        ):
            RepositoryWorkflow(root).structural_validate()


if __name__ == "__main__":
    unittest.main()
