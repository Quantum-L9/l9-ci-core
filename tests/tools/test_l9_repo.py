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

from l9_repo.__main__ import (  # noqa: E402
    PHASES,
    V2_FACADE_NAME,
    V2_SCHEMA_NAME,
    RepositoryWorkflow,
    WorkflowError,
    classify_make_phase_returncode,
    validate_config_data,
)


def run_git(root: pathlib.Path, *args: str) -> None:
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


def contract() -> dict[str, object]:
    return {
        "schema": V2_SCHEMA_NAME,
        "facade": V2_FACADE_NAME,
        "required_phases": list(PHASES),
    }


def fixture(root: pathlib.Path, *, style: str = "python") -> None:
    (root / ".l9").mkdir(parents=True)
    (root / ".l9" / "repo-workflow.json").write_text(
        json.dumps(contract(), indent=2) + "\n", encoding="utf-8"
    )
    (root / "Makefile").write_bytes(
        (ROOT / "tools/l9_repo/Makefile.template").read_bytes()
    )
    lines = [".PHONY: " + " ".join(f"repo-{phase}" for phase in PHASES), ""]
    for phase in PHASES:
        marker = f"{style}:{phase}"
        lines.extend([f"repo-{phase}:", f"\t@printf '{marker}\\n' >> order.log", ""])
    (root / "Repo.mk").write_text("\n".join(lines), encoding="utf-8")
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.email", "tests@example.com")
    run_git(root, "config", "user.name", "Tests")
    # Git may launch maintenance after a fixture commit. That worker can still
    # write below .git while TemporaryDirectory removes the fixture, producing
    # an intermittent CI-only cleanup failure. Fixtures are short-lived and
    # isolated, so neither automatic garbage collection nor maintenance is
    # useful here; disable both before their first commit.
    run_git(root, "config", "gc.auto", "0")
    run_git(root, "config", "maintenance.auto", "false")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "fixture")


class V2ContractTests(unittest.TestCase):
    def test_core_self_host_contract_is_v2_and_generic(self) -> None:
        data = json.loads((ROOT / ".l9/repo-workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(data, contract())
        self.assertNotIn("Quantum-L9/l9-ci-core", json.dumps(data))
        self.assertIs(validate_config_data(data), data)

    def test_arbitrary_consumer_contract_validates(self) -> None:
        data = contract()
        self.assertIs(validate_config_data(data), data)

    def test_unknown_and_prohibited_v1_fields_fail_closed(self) -> None:
        for key, value in {
            "commands": {"check": [["ruff", "check", "."]]},
            "push": {"run_check": True},
            "pull_request": {"base": "main"},
            "authority": {},
            "metadata": {"beneficiary": "Quantum-L9/l9-ci-core"},
            "core_revision": "deadbeef",
        }.items():
            with self.subTest(key=key):
                data = contract()
                data[key] = value
                with self.assertRaisesRegex(WorkflowError, "unsupported keys"):
                    validate_config_data(data)

    def test_required_phase_order_is_not_consumer_selectable(self) -> None:
        data = contract()
        data["required_phases"] = list(reversed(PHASES))
        with self.assertRaisesRegex(WorkflowError, "in order"):
            validate_config_data(data)

    def test_embedded_schema_rejects_unknown_fields(self) -> None:
        schema = json.loads(
            (ROOT / "tools/l9_repo/repo-workflow-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            "https://quantum-l9.dev/schemas/repository-execution/v2", schema["$id"]
        )


class CompilerAndFacadeTests(unittest.TestCase):
    def test_canonical_facade_is_deterministic(self) -> None:
        self.assertEqual(
            (ROOT / "Makefile").read_bytes(),
            (ROOT / "tools/l9_repo/Makefile.template").read_bytes(),
        )

    def test_reconcile_preserves_existing_repo_mk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fixture(root)
            original = (root / "Repo.mk").read_bytes()
            (root / "Makefile").write_text("drift\n", encoding="utf-8")
            workflow = RepositoryWorkflow(root)
            workflow.reconcile()
            self.assertEqual(original, (root / "Repo.mk").read_bytes())
            self.assertEqual(
                (ROOT / "tools/l9_repo/Makefile.template").read_bytes(),
                (root / "Makefile").read_bytes(),
            )

    def test_failing_make_recipe_exit_two_is_a_repository_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "Makefile").write_text("check:\n\tfalse\n", encoding="utf-8")
            result = subprocess.run(
                ["make", "check"], cwd=root, capture_output=True, text=True, check=False
            )
        self.assertEqual(2, result.returncode)
        self.assertEqual("finding", classify_make_phase_returncode(result.returncode))

    def test_verify_generated_detects_drift_without_mutating_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fixture(root)
            (root / "Makefile").write_text("drift\n", encoding="utf-8")
            before = (root / "Makefile").read_bytes()
            with self.assertRaisesRegex(WorkflowError, "Makefile drift"):
                RepositoryWorkflow(root).verify_generated()
            self.assertEqual(before, (root / "Makefile").read_bytes())

    def test_missing_required_repo_leaf_is_a_contract_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fixture(root)
            (root / "Repo.mk").write_text("repo-setup:\n\t@:\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "repo-validate"):
                RepositoryWorkflow(root).verify_generated()

    def test_consumer_vendored_core_files_do_not_change_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fixture(root)
            vendored = root / "tools/l9_repo"
            vendored.mkdir(parents=True)
            (vendored / "Makefile.template").write_text("malicious\n", encoding="utf-8")
            RepositoryWorkflow(root).verify_generated()


class MigrationTests(unittest.TestCase):
    def test_v1_migration_translates_commands_into_repo_mk_and_drops_policy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / ".l9").mkdir()
            legacy = {
                "schema_version": 1,
                "metadata": {"beneficiary": "Quantum-L9/l9-ci-core"},
                "authority": {"change_policy": "Core-only"},
                "commands": {
                    phase: [["@python", "-c", f"print('{phase}')"]] for phase in PHASES
                },
                "push": {"run_check": True},
            }
            (root / ".l9/repo-workflow.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )
            run_git(root, "init", "-b", "main")
            run_git(root, "config", "user.email", "tests@example.com")
            run_git(root, "config", "user.name", "Tests")
            run_git(root, "add", ".")
            run_git(root, "commit", "-m", "legacy")
            RepositoryWorkflow(root).migrate_v1()
            migrated = json.loads((root / ".l9/repo-workflow.json").read_text())
            self.assertEqual(contract(), migrated)
            implementation = (root / "Repo.mk").read_text(encoding="utf-8")
            self.assertIn("repo-check", implementation)
            self.assertNotIn("beneficiary", json.dumps(migrated))
            self.assertNotIn("authority", json.dumps(migrated))

    def test_migration_never_overwrites_existing_repo_mk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fixture(root)
            existing = (root / "Repo.mk").read_bytes()
            legacy = {
                "schema_version": 1,
                "commands": {phase: [["@python", "-c", "pass"]] for phase in PHASES},
            }
            (root / ".l9/repo-workflow.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )
            RepositoryWorkflow(root).migrate_v1()
            self.assertEqual(existing, (root / "Repo.mk").read_bytes())


class CorePolicyTests(unittest.TestCase):
    def test_core_local_policy_is_not_part_of_v2_contract(self) -> None:
        policy = json.loads(
            (ROOT / ".l9/core-repo-policy.json").read_text(encoding="utf-8")
        )
        self.assertEqual("l9.core-repository-policy/v1", policy["schema"])
        self.assertIn("change_policy", policy)
        portable = json.loads(
            (ROOT / ".l9/repo-workflow.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(portable), {"schema", "facade", "required_phases"})

    def test_core_generic_verifier_never_uses_core_policy_for_a_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fixture(root)
            self.assertFalse((root / ".l9/core-repo-policy.json").exists())
            RepositoryWorkflow(root).verify_generated()


if __name__ == "__main__":
    unittest.main()
