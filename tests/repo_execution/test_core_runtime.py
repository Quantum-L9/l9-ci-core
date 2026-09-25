"""Core's local runtime: core-validate, agent-check, status, clean, lock, CLI.

Fixtures copy this repository's tracked tree into a fresh git repository and
replace ``Repo.mk`` with fast, self-contained leaves, so ``make validate``,
``make check`` and ``make test`` inside the fixture never recurse into the
real suite or the real toolchain.
"""

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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import ROOT, git, init_repo  # noqa: E402

from l9_repo import locking  # noqa: E402
from l9_repo.__main__ import (  # noqa: E402
    CORE_POLICY_PATH,
    MANIFEST_CHECK_ENV,
    AgentCheckFailure,
    CoreRepositoryExecution,
    WorkflowError,
    main,
    verify_checksum_manifest,
)
from l9_repo.contract import ContractError  # noqa: E402


def leaf(exit_code: int = 0, output: str = "") -> str:
    # Double quotes inside the single-quoted shell word: the recipe is shell text.
    return f"\t@$(PYTHON) -c 'print(\"{output}\"); raise SystemExit({exit_code})'"


def simple_repo_mk(
    *,
    validate: str = leaf(),
    check: str = leaf(),
    test: str = leaf(),
) -> str:
    return (
        "PYTHON ?= python3\n"
        ".PHONY: repo-setup repo-validate repo-check repo-test\n"
        f"repo-setup:\n{leaf()}\n"
        f"repo-validate:\n{validate}\n"
        f"repo-check:\n{check}\n"
        f"repo-test:\n{test}\n"
    )


def simple_command(exit_code: int = 0, output: str = "") -> list[str]:
    code = f"print({output!r}); raise SystemExit({exit_code})"
    return ["@python", "-c", code]


def copy_tracked_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Copy only tracked files: no .git, no symlinked trees, no untracked files."""

    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=source, capture_output=True, check=False
    )
    if listed.returncode != 0:
        raise AssertionError("cannot enumerate tracked files; fixture needs git")
    for relative in listed.stdout.decode("utf-8").split("\0"):
        if not relative:
            continue
        origin = source / relative
        if not origin.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)


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


def write_policy(root: pathlib.Path, policy: dict[str, object]) -> None:
    (root / CORE_POLICY_PATH).write_text(
        json.dumps(policy, indent=2) + "\n", encoding="utf-8"
    )


def simplify_gates(root: pathlib.Path) -> dict[str, object]:
    policy = json.loads((root / CORE_POLICY_PATH).read_text(encoding="utf-8"))
    for gate in policy["change_policy"]["gates"].values():
        gate["commands"] = [simple_command()]
    write_policy(root, policy)
    return policy


def initialize_fixture(root: pathlib.Path) -> None:
    (root / "AGENTS.md").write_text(
        "\n".join(
            [
                "[architecture](.l9/architecture.yaml)",
                "[ownership](.l9/ownership.yaml)",
                "[compatibility](.l9/sdk-compatibility.yaml)",
                "[org-runtime-contract](.l9/org-runtime-contract.yaml)",
                "[org-runtime-interface](.l9/org-runtime-interface.yaml)",
                "[contract](.l9/repo-workflow.json)",
                "[policy](.l9/core-repo-policy.json)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(
        "artifacts/\n__pycache__/\n*.pyc\n", encoding="utf-8"
    )
    (root / "Repo.mk").write_text(simple_repo_mk(), encoding="utf-8")
    simplify_gates(root)
    regenerate_manifest(root)


def make_fixture() -> tuple[tempfile.TemporaryDirectory[str], pathlib.Path]:
    temporary = tempfile.TemporaryDirectory()
    root = pathlib.Path(temporary.name)
    copy_tracked_tree(ROOT, root)
    initialize_fixture(root)
    init_repo(root)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "checkout", "-q", "-b", "feature")
    return temporary, root


def commit_fixture(root: pathlib.Path, message: str) -> None:
    regenerate_manifest(root)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", message)


class FixtureIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary, self.root = make_fixture()
        self.addCleanup(temporary.cleanup)

    def test_fixture_history_starts_at_a_single_base_commit(self) -> None:
        self.assertEqual("1", git(self.root, "rev-list", "--count", "HEAD").strip())

    def test_fixture_inherits_no_remote_or_local_refs(self) -> None:
        heads = set(
            git(
                self.root, "for-each-ref", "--format=%(refname:short)", "refs/heads"
            ).split()
        )
        remotes = set(
            git(
                self.root, "for-each-ref", "--format=%(refname:short)", "refs/remotes"
            ).split()
        )
        self.assertEqual({"main", "feature"}, heads)
        self.assertEqual({"origin/main"}, remotes)

    def test_fixture_is_a_valid_core_consumer(self) -> None:
        core = CoreRepositoryExecution(self.root)
        core.core_validate()


class CoreValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary, self.root = make_fixture()
        self.addCleanup(temporary.cleanup)

    def test_checksum_manifest_mismatch_is_rejected_by_default(self) -> None:
        (self.root / "SECURITY.md").write_text("tampered\n", encoding="utf-8")
        with mock.patch.dict(os.environ):
            os.environ.pop(MANIFEST_CHECK_ENV, None)
            with self.assertRaisesRegex(WorkflowError, "checksum mismatch"):
                CoreRepositoryExecution(self.root).core_validate()

    def test_checksum_manifest_is_skipped_only_when_switched_off(self) -> None:
        (self.root / "SECURITY.md").write_text("tampered\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {MANIFEST_CHECK_ENV: "0"}):
            CoreRepositoryExecution(self.root).core_validate()
        with self.assertRaisesRegex(WorkflowError, "checksum mismatch"):
            verify_checksum_manifest(self.root)

    def test_checksum_manifest_rejects_symlinked_entry(self) -> None:
        outside = self.root.parent / "outside-l9-test.txt"
        outside.write_text("outside\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.root / "linked.txt"
        link.symlink_to(outside)
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        with (self.root / "MANIFEST.sha256").open("a", encoding="utf-8") as handle:
            handle.write(f"{digest}  linked.txt\n")
        with self.assertRaisesRegex(WorkflowError, "symlinked"):
            verify_checksum_manifest(self.root)

    def test_missing_authority_document_is_rejected(self) -> None:
        (self.root / ".l9/ownership.yaml").unlink()
        regenerate_manifest(self.root)
        with self.assertRaisesRegex(WorkflowError, "missing authoritative file"):
            CoreRepositoryExecution(self.root).core_validate()

    def test_missing_dependency_manifest_is_rejected(self) -> None:
        (self.root / "requirements-repo-runtime.txt").unlink()
        regenerate_manifest(self.root)
        with self.assertRaisesRegex(WorkflowError, "missing dependency manifest"):
            CoreRepositoryExecution(self.root).core_validate()

    def test_unreferenced_policy_document_is_rejected(self) -> None:
        agents = self.root / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8").replace(
                "[policy](.l9/core-repo-policy.json)\n", ""
            ),
            encoding="utf-8",
        )
        regenerate_manifest(self.root)
        with self.assertRaisesRegex(
            WorkflowError, "core-repo-policy.json is not referenced"
        ):
            CoreRepositoryExecution(self.root).core_validate()

    def test_makefile_drift_fails_core_validation(self) -> None:
        with (self.root / "Makefile").open("a", encoding="utf-8") as stream:
            stream.write("\npr:\n\t@echo no\n")
        regenerate_manifest(self.root)
        with self.assertRaisesRegex(ContractError, "drift"):
            CoreRepositoryExecution(self.root).core_validate()

    def test_v1_contract_is_not_a_valid_core_contract(self) -> None:
        (self.root / ".l9/repo-workflow.json").write_text(
            '{"schema_version": 1, "commands": {}}\n', encoding="utf-8"
        )
        regenerate_manifest(self.root)
        with self.assertRaises(ContractError):
            CoreRepositoryExecution(self.root).core_validate()


class AgentCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary, self.root = make_fixture()
        self.addCleanup(temporary.cleanup)

    def evidence(self) -> dict[str, object]:
        return json.loads(
            (self.root / "artifacts/agent-check-evidence.json").read_text(
                encoding="utf-8"
            )
        )

    def test_agent_check_runs_the_facade_phases_and_writes_both_reports(self) -> None:
        core = CoreRepositoryExecution(self.root)
        core.agent_check(explicit=["MANIFEST.sha256"])
        payload = self.evidence()
        self.assertEqual(payload["overall_exit_code"], 0)
        names = [step["name"] for step in payload["steps"]]
        self.assertEqual(
            [
                "structural-validate",
                "make:validate",
                "make:check",
                "make:test",
                "non-mutation-check",
            ],
            names,
        )
        self.assertTrue((self.root / "artifacts/agent-check-evidence.md").is_file())

    def test_failing_phases_are_blocking_findings(self) -> None:
        (self.root / "Repo.mk").write_text(
            simple_repo_mk(
                validate=leaf(1, "validate failed"),
                check=leaf(1, "check failed"),
                test=leaf(0, "test passed"),
            ),
            encoding="utf-8",
        )
        commit_fixture(self.root, "configure failures")
        with self.assertRaisesRegex(AgentCheckFailure, "2 blocking finding"):
            CoreRepositoryExecution(self.root).agent_check(explicit=["MANIFEST.sha256"])
        payload = self.evidence()
        self.assertEqual(payload["overall_exit_code"], 1)
        self.assertEqual(
            [step["classification"] for step in payload["steps"] if step["command"]],
            ["finding", "finding", "pass"],
        )
        self.assertIn("check failed", payload["steps"][2]["stdout"])

    def test_unallowlisted_gate_executable_is_rejected_before_running(self) -> None:
        policy = json.loads((self.root / CORE_POLICY_PATH).read_text(encoding="utf-8"))
        policy["change_policy"]["gates"]["workflow"]["commands"] = [
            ["definitely-not-a-tool"]
        ]
        write_policy(self.root, policy)
        commit_fixture(self.root, "arbitrary executable")
        with self.assertRaisesRegex(WorkflowError, "argv-only allowlist"):
            CoreRepositoryExecution(self.root).agent_check(explicit=["MANIFEST.sha256"])
        self.assertFalse((self.root / "artifacts/agent-check-evidence.json").is_file())

    def test_committed_feature_diff_selects_targeted_gates(self) -> None:
        workflow = self.root / ".github/workflows/example.yml"
        workflow.write_text("name: example\n", encoding="utf-8")
        test_path = self.root / "tests/workflows/test_example.py"
        test_path.write_text("# companion\n", encoding="utf-8")
        commit_fixture(self.root, "workflow change")
        CoreRepositoryExecution(self.root).agent_check()
        payload = self.evidence()
        self.assertIn(".github/workflows/example.yml", payload["changed_files"])
        names = [step["name"] for step in payload["steps"]]
        self.assertIn("change-gate:workflow:1", names)

    def test_missing_companion_is_a_blocking_finding(self) -> None:
        workflow = self.root / ".github/workflows/example.yml"
        workflow.write_text("name: example\n", encoding="utf-8")
        commit_fixture(self.root, "workflow change without tests")
        with self.assertRaisesRegex(AgentCheckFailure, "blocking finding"):
            CoreRepositoryExecution(self.root).agent_check()
        rules = [item["rule_id"] for item in self.evidence()["companion_findings"]]
        self.assertEqual(["workflow-tests"], rules)

    def test_agent_check_does_not_dirty_tracked_worktree(self) -> None:
        CoreRepositoryExecution(self.root).agent_check(explicit=["MANIFEST.sha256"])
        self.assertEqual("", git(self.root, "status", "--porcelain").strip())

    def test_agent_check_fails_if_a_phase_mutates_tracked_content(self) -> None:
        mutate = (
            "\t@$(PYTHON) -c 'from pathlib import Path; "
            'Path("README.md").write_text("mutated\\n")\''
        )
        (self.root / "Repo.mk").write_text(
            simple_repo_mk(check=mutate), encoding="utf-8"
        )
        commit_fixture(self.root, "mutating phase")
        with self.assertRaisesRegex(WorkflowError, "infrastructure"):
            CoreRepositoryExecution(self.root).agent_check(explicit=["MANIFEST.sha256"])
        check = next(
            step
            for step in self.evidence()["steps"]
            if step["name"] == "non-mutation-check"
        )
        self.assertEqual(check["classification"], "infrastructure")
        self.assertIn("worktree content changed", check["stderr"])

    def test_structural_failure_is_infrastructure_not_a_finding(self) -> None:
        (self.root / "SECURITY.md").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowError, "infrastructure"):
            CoreRepositoryExecution(self.root).agent_check(explicit=["MANIFEST.sha256"])
        self.assertEqual(self.evidence()["overall_exit_code"], 2)


class LocalOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = CoreRepositoryExecution(ROOT)

    def fake_git(self, *, fetch_ok: bool, verify: dict[str, bool] | None = None):
        def _git(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args == ("branch", "--show-current"):
                return subprocess.CompletedProcess(["git"], 0, "feature\n", "")
            if args == ("rev-parse", "HEAD"):
                return subprocess.CompletedProcess(["git"], 0, "abc\n", "")
            if args[:2] == ("fetch", "--prune"):
                return subprocess.CompletedProcess(
                    ["git"], 0 if fetch_ok else 1, "", "" if fetch_ok else "offline"
                )
            if args[:2] == ("rev-parse", "--verify"):
                ok = (verify or {}).get(args[2], False)
                return subprocess.CompletedProcess(
                    ["git"], 0 if ok else 1, "ok\n" if ok else "", ""
                )
            if args[:3] == ("rev-list", "--left-right", "--count"):
                return subprocess.CompletedProcess(["git"], 0, "2 3\n", "")
            if args == ("status", "--porcelain"):
                return subprocess.CompletedProcess(["git"], 0, "", "")
            raise AssertionError(args)

        return _git

    def status_payload(self, fake) -> dict[str, object]:
        output = io.StringIO()
        with (
            mock.patch.object(self.core, "_ensure_repository_root"),
            mock.patch.object(self.core, "git", side_effect=fake),
            contextlib.redirect_stdout(output),
        ):
            self.core.status()
        return json.loads(output.getvalue())

    def test_status_reports_live_divergence(self) -> None:
        payload = self.status_payload(
            self.fake_git(fetch_ok=True, verify={"origin/feature": True})
        )
        self.assertEqual(payload["remote_freshness"], "fresh")
        self.assertEqual(payload["comparison_ref"], "origin/feature")
        self.assertEqual(payload["behind"], 2)
        self.assertEqual(payload["ahead"], 3)
        self.assertEqual(payload["comparison_source"], "live")

    def test_status_offline_falls_back_to_the_policy_comparison_ref(self) -> None:
        payload = self.status_payload(
            self.fake_git(fetch_ok=False, verify={"origin/main": True})
        )
        self.assertEqual(payload["remote_freshness"], "unknown_offline")
        self.assertEqual(payload["comparison_ref"], "origin/main")
        self.assertEqual(payload["comparison_source"], "cached")
        self.assertEqual(payload["fetch_error"], "offline")

    def test_status_without_any_remote_ref_reports_unavailable(self) -> None:
        payload = self.status_payload(self.fake_git(fetch_ok=False))
        self.assertIsNone(payload["comparison_ref"])
        self.assertEqual(payload["comparison_source"], "unavailable")

    def test_clean_never_escapes_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = CoreRepositoryExecution(pathlib.Path(temporary))
            with (
                mock.patch.object(core, "_ensure_repository_root"),
                mock.patch.object(
                    core, "policy", return_value={"clean_paths": ["../x"]}
                ),
            ):
                with self.assertRaisesRegex(WorkflowError, "unsafe clean path"):
                    core.clean()

    def test_workspace_inside_repo_is_rejected(self) -> None:
        completed = subprocess.CompletedProcess(["git"], 0, str(ROOT) + "\n", "")
        core = CoreRepositoryExecution(ROOT / "tests")
        with mock.patch.object(core, "git", return_value=completed):
            with self.assertRaisesRegex(WorkflowError, "not repository root"):
                core._ensure_repository_root()

    def test_non_git_workspace_is_taxonomy_two(self) -> None:
        result = subprocess.CompletedProcess(["git"], 128, "", "not a repo")
        with mock.patch.object(self.core, "git", return_value=result):
            with self.assertRaisesRegex(WorkflowError, "not a repo"):
                self.core._ensure_repository_root()

    def test_doctor_requires_git_and_make(self) -> None:
        with (
            mock.patch.object(self.core, "_ensure_repository_root"),
            mock.patch("l9_repo.__main__.shutil.which", return_value=None),
        ):
            with self.assertRaisesRegex(WorkflowError, "missing tools: git, make"):
                self.core.doctor()


class CliTests(unittest.TestCase):
    def test_cli_validate_reaches_the_generic_validator(self) -> None:
        with mock.patch.object(CoreRepositoryExecution, "validate") as validate:
            self.assertEqual(main(["--workspace", str(ROOT), "validate"]), 0)
        validate.assert_called_once_with()

    def test_cli_reconcile_is_locked_and_idempotent(self) -> None:
        temporary, root = make_fixture()
        self.addCleanup(temporary.cleanup)
        (root / "Makefile").write_text("drift\n", encoding="utf-8")
        self.assertEqual(main(["--workspace", str(root), "reconcile"]), 0)
        self.assertEqual(main(["--workspace", str(root), "reconcile"]), 0)
        self.assertEqual("", git(root, "status", "--porcelain").strip())

    def test_cli_change_policy_missing_context_exits_two(self) -> None:
        temporary, root = make_fixture()
        self.addCleanup(temporary.cleanup)
        git(root, "checkout", "-q", "main")
        git(root, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(main(["--workspace", str(root), "change-policy"]), 2)

    def test_main_reports_workflow_error_as_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(
                CoreRepositoryExecution, "doctor", side_effect=WorkflowError("broken")
            ),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main(["--workspace", str(ROOT), "doctor"]), 2)
        self.assertIn("broken", stderr.getvalue())

    def test_main_reports_findings_as_one(self) -> None:
        with mock.patch.object(
            CoreRepositoryExecution, "agent_check", side_effect=AgentCheckFailure("f")
        ):
            self.assertEqual(main(["--workspace", str(ROOT), "agent-check"]), 1)


class LockPrimitiveTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
