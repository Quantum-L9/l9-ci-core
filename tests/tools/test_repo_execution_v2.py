"""Repository Execution V2 acceptance matrix and architecture tripwires.

These tests pin the ownership model that superseded Compiler V2 (#186):
Core owns the contract, the compiler, the generated Makefile facade, and the
verifier; each repository owns Repo.mk. A regression toward generated Repo.mk,
a second compiler, command arrays in the contract, or consumer-vendored Core
runtime fails here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.__main__ import (  # noqa: E402
    FACADE_TARGETS,
    PHASES,
    RESERVED_GOVERNANCE_TARGETS,
    V2_FACADE_NAME,
    V2_SCHEMA_NAME,
    RepositoryWorkflow,
    WorkflowError,
    make_target_declarations,
)

TEMPLATE = ROOT / "tools/l9_repo/Makefile.template"
RUNNER = ROOT / ".github/actions/run-repository-verification/run.py"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env=GIT_ENV,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


def v2_contract() -> dict[str, object]:
    return {
        "schema": V2_SCHEMA_NAME,
        "facade": V2_FACADE_NAME,
        "required_phases": list(PHASES),
    }


def init_repo(root: pathlib.Path) -> None:
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "tests@example.com")
    git(root, "config", "user.name", "Tests")
    git(root, "config", "gc.auto", "0")
    git(root, "config", "maintenance.auto", "false")


def commit_all(root: pathlib.Path, message: str) -> None:
    git(root, "add", ".")
    git(root, "commit", "-m", message)


def consumer(root: pathlib.Path, repo_mk: str | None = None) -> None:
    """A minimal ordinary V2 consumer: contract, generated Makefile, Repo.mk."""

    (root / ".l9").mkdir(parents=True)
    (root / ".l9/repo-workflow.json").write_text(
        json.dumps(v2_contract(), indent=2) + "\n", encoding="utf-8"
    )
    (root / "Makefile").write_bytes(TEMPLATE.read_bytes())
    if repo_mk is None:
        repo_mk = "".join(
            f"repo-{phase}:\n\t@printf '{phase}\\n' >> phases.log\n\n"
            for phase in PHASES
        )
    (root / "Repo.mk").write_text(repo_mk, encoding="utf-8")
    init_repo(root)
    commit_all(root, "consumer")


def load_runner():
    spec = importlib.util.spec_from_file_location("l9_verify_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_verifier(
    runner, workspace: pathlib.Path, mode: str, workflow_class=None
) -> dict[str, str]:
    with tempfile.NamedTemporaryFile() as output:
        environment = {
            "L9_REPOSITORY_WORKSPACE": str(workspace),
            "GITHUB_OUTPUT": output.name,
            "L9_REPOSITORY_CONTRACT_MODE": mode,
        }
        with unittest.mock.patch.dict(os.environ, environment, clear=False):
            if workflow_class is None:
                code = runner.main()
            else:
                with unittest.mock.patch.object(
                    runner, "RepositoryWorkflow", workflow_class
                ):
                    code = runner.main()
        text = pathlib.Path(output.name).read_text(encoding="utf-8")
    assert code == 0
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


class ReconcileDeterminismTests(unittest.TestCase):
    def test_second_reconcile_produces_zero_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(root)
            (root / "Makefile").write_text("drift\n", encoding="utf-8")
            workflow = RepositoryWorkflow(root)
            workflow.reconcile()
            self.assertEqual("", git(root, "status", "--porcelain"))
            workflow.reconcile()
            self.assertEqual("", git(root, "status", "--porcelain"))
            self.assertEqual(TEMPLATE.read_bytes(), (root / "Makefile").read_bytes())

    def test_manual_makefile_edit_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(root)
            with (root / "Makefile").open("a", encoding="utf-8") as stream:
                stream.write("\nextra:\n\t@true\n")
            with self.assertRaisesRegex(WorkflowError, "Makefile drift"):
                RepositoryWorkflow(root).verify_generated()

    def test_legitimate_repo_mk_edit_survives_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(root)
            with (root / "Repo.mk").open("a", encoding="utf-8") as stream:
                stream.write("# owned by this repository\nbench:\n\t@true\n")
            edited = (root / "Repo.mk").read_bytes()
            workflow = RepositoryWorkflow(root)
            workflow.reconcile()
            workflow.reconcile()
            self.assertEqual(edited, (root / "Repo.mk").read_bytes())
            workflow.verify_generated()


class RepoMkBoundaryTests(unittest.TestCase):
    def test_missing_repo_mk_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(root)
            (root / "Repo.mk").unlink()
            with self.assertRaisesRegex(WorkflowError, "missing .*Repo.mk"):
                RepositoryWorkflow(root).verify_generated()

    def test_each_missing_required_leaf_is_named(self) -> None:
        for missing in PHASES:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                consumer(
                    root,
                    "".join(
                        f"repo-{phase}:\n\t@:\n\n"
                        for phase in PHASES
                        if phase != missing
                    ),
                )
                with self.assertRaisesRegex(WorkflowError, f"repo-{missing}"):
                    RepositoryWorkflow(root).verify_generated()

    def test_repo_mk_may_not_redefine_facade_or_governance_targets(self) -> None:
        leaves = "".join(f"repo-{phase}:\n\t@:\n\n" for phase in PHASES)
        for target in sorted(FACADE_TARGETS | RESERVED_GOVERNANCE_TARGETS):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                consumer(root, leaves + f"{target}:\n\t@true\n")
                with self.assertRaisesRegex(
                    WorkflowError, rf"Repo\.mk:\d+ defines .*'{target}'"
                ):
                    RepositoryWorkflow(root).verify_generated()

    def test_target_parser_ignores_assignments_recipes_and_special_targets(
        self,
    ) -> None:
        text = (
            "PYTHON ?= python3\n"
            "TOOL := $(PYTHON) -m tool\n"
            "LATE ::= value\n"
            ".PHONY: \\\n"
            "\trepo-setup\n"
            "repo-setup repo-test: ## two targets\n"
            "\t@echo pr: not a rule\n"
            "lint: repo-check\n"
        )
        self.assertEqual(
            [(6, "repo-setup"), (6, "repo-test"), (8, "lint")],
            make_target_declarations(text),
        )


class VerifierModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_runner()

    def v1_workflow(self, calls: list[str]):
        class V1Workflow:
            def __init__(self, workspace: pathlib.Path) -> None:
                self.workspace = workspace

            def contract_version(self) -> str:
                return "v1"

            def execute_legacy_phase(self, phase: str) -> None:
                calls.append(phase)

        return V1Workflow

    def write_contract(self, root: pathlib.Path, data: object) -> None:
        (root / ".l9").mkdir(parents=True, exist_ok=True)
        (root / ".l9/repo-workflow.json").write_text(json.dumps(data), encoding="utf-8")

    def test_v2_consumer_passes_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(root)
            outputs = run_verifier(self.runner, root, "required")
            self.assertEqual("V2_PASS", outputs["result"])
            self.assertEqual("pass", outputs["status"])
            self.assertEqual(
                list(PHASES),
                (root / "phases.log").read_text(encoding="utf-8").split(),
            )

    def test_v1_runs_only_in_migration_mode_with_a_distinct_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_contract(root, {"schema_version": 1})
            calls: list[str] = []
            outputs = run_verifier(
                self.runner, root, "migration", self.v1_workflow(calls)
            )
            self.assertEqual(list(PHASES), calls)
            self.assertEqual("V1_COMPAT", outputs["result"])
            self.assertEqual("v1_compat", outputs["status"])
            self.assertEqual("v1", outputs["contract-version"])

    def test_v1_is_rejected_in_required_mode_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write_contract(root, {"schema_version": 1})
            calls: list[str] = []
            outputs = run_verifier(
                self.runner, root, "required", self.v1_workflow(calls)
            )
            self.assertEqual([], calls)
            self.assertEqual("CONTRACT_INVALID", outputs["result"])
            self.assertEqual("contract_failure", outputs["status"])

    def test_missing_contract_is_fatal_only_in_required_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            migration = run_verifier(self.runner, root, "migration")
            required = run_verifier(self.runner, root, "required")
        self.assertEqual("CONTRACT_MISSING", migration["result"])
        self.assertEqual("legacy_not_applicable", migration["status"])
        self.assertEqual("CONTRACT_MISSING", required["result"])
        self.assertEqual("missing_repository_contract", required["status"])

    def test_invalid_v2_contract_is_contract_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(root)
            data = v2_contract()
            data["commands"] = {"test": [["pytest"]]}
            self.write_contract(root, data)
            outputs = run_verifier(self.runner, root, "required")
            self.assertEqual("CONTRACT_INVALID", outputs["result"])
            self.assertFalse((root / "phases.log").exists())

    def test_failing_phase_is_technical_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(
                root,
                "".join(
                    f"repo-{phase}:\n\t@{'false' if phase == 'check' else ':'}\n\n"
                    for phase in PHASES
                ),
            )
            outputs = run_verifier(self.runner, root, "required")
            self.assertEqual("TECHNICAL_FAILURE", outputs["result"])


class MigrationPreservationTests(unittest.TestCase):
    LEGACY_COMMANDS = {
        "setup": [["@python", "-c", "open('setup.log','w').write('setup')"]],
        "validate": [["@python", "-c", "open('validate.log','w').write('validate')"]],
        "check": [
            ["@python", "-c", "open('check-1.log','w').write('ruff')"],
            ["@python", "-c", "open('check-2.log','w').write('mypy')"],
        ],
        "test": [["@python", "-c", "open('test.log','w').write('test')"]],
    }

    def legacy(self, root: pathlib.Path, commands: object) -> None:
        (root / ".l9").mkdir()
        (root / ".l9/repo-workflow.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "commands": commands,
                    "clean_paths": [".cache"],
                    "push": {"run_check": True},
                }
            ),
            encoding="utf-8",
        )
        init_repo(root)
        commit_all(root, "legacy")

    def test_migration_preserves_every_command_and_retires_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.legacy(root, self.LEGACY_COMMANDS)
            RepositoryWorkflow(root).migrate_v1()

            declared = json.loads(
                (root / ".l9/repo-workflow.json").read_text(encoding="utf-8")
            )
            self.assertEqual(v2_contract(), declared)
            self.assertNotIn("@python", json.dumps(declared))

            commit_all(root, "migrated")
            for phase in PHASES:
                result = subprocess.run(
                    ["make", "--no-print-directory", phase],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
            for name in (
                "setup.log",
                "validate.log",
                "check-1.log",
                "check-2.log",
                "test.log",
            ):
                self.assertTrue((root / name).is_file(), name)

            for name in root.glob("*.log"):
                name.unlink()
            workflow = RepositoryWorkflow(root)
            workflow.reconcile()
            workflow.reconcile()
            self.assertEqual("", git(root, "status", "--porcelain"))

    def test_migration_fails_closed_when_preservation_is_unsafe(self) -> None:
        unsafe = {
            "missing phase": {
                phase: argv
                for phase, argv in self.LEGACY_COMMANDS.items()
                if phase != "test"
            },
            "executable outside allowlist": {
                **self.LEGACY_COMMANDS,
                "check": [["curl", "https://example.invalid"]],
            },
        }
        for label, commands in unsafe.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                self.legacy(root, commands)
                before = (root / ".l9/repo-workflow.json").read_bytes()
                with self.assertRaises(WorkflowError):
                    RepositoryWorkflow(root).migrate_v1()
                self.assertEqual(before, (root / ".l9/repo-workflow.json").read_bytes())
                self.assertFalse((root / "Repo.mk").exists())


class ConsumerPortabilityTests(unittest.TestCase):
    """An ordinary consumer runs the ABI with no Core runtime, schema, or CI."""

    @staticmethod
    def executable(path: pathlib.Path, body: str) -> None:
        path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
        path.chmod(0o755)

    def test_consumer_executes_abi_without_vendored_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = pathlib.Path(temporary)
            root = sandbox / "consumer"
            root.mkdir()
            fake_bin = sandbox / "bin"
            fake_bin.mkdir()
            native = sandbox / "native.log"
            dispatcher = sandbox / "dispatcher.log"
            forbidden = sandbox / "forbidden.log"
            self.executable(fake_bin / "native-tool", 'echo "$@" >> "$NATIVE_LOG"\n')
            self.executable(fake_bin / "l9", 'echo "$@" > "$DISPATCH_LOG"\n')
            consumer(
                root,
                "".join(
                    f"repo-{phase}:\n\t@native-tool {phase}\n\n" for phase in PHASES
                ),
            )
            self.assertEqual(
                {".git", ".l9", "Makefile", "Repo.mk"},
                {path.name for path in root.iterdir()},
            )
            self.assertEqual(
                ["repo-workflow.json"], [p.name for p in (root / ".l9").iterdir()]
            )
            for tool in ("git", "gh"):
                self.executable(
                    fake_bin / tool, f'echo "{tool} $*" >> "$FORBIDDEN_LOG"\nexit 99\n'
                )

            environment = {
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "NATIVE_LOG": str(native),
                "DISPATCH_LOG": str(dispatcher),
                "FORBIDDEN_LOG": str(forbidden),
                "L9": str(fake_bin / "l9"),
            }
            for target in (*PHASES, "pr"):
                result = subprocess.run(
                    ["make", "--no-print-directory", target],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(list(PHASES), native.read_text().split())
            self.assertEqual("pr\n", dispatcher.read_text())
            self.assertFalse(forbidden.exists())


class CoreSelfHostingTests(unittest.TestCase):
    """Core reaches its own tooling only through the facade and Repo.mk."""

    EXPECTED_RECIPES = {
        "setup": "pip install -r requirements-ci.txt",
        "validate": "core-validate",
        "check": "check_toolchain_versions.py",
        "test": "unittest discover",
    }

    def test_each_phase_resolves_to_core_repo_mk_recipe(self) -> None:
        for phase, fragment in self.EXPECTED_RECIPES.items():
            with self.subTest(phase=phase):
                result = subprocess.run(
                    ["make", "--no-print-directory", "-n", phase],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn(fragment, result.stdout)

    def test_core_is_a_valid_v2_consumer_of_its_own_abi(self) -> None:
        RepositoryWorkflow(ROOT).verify_generated()


class ArchitectureTripwireTests(unittest.TestCase):
    """Fail on any regression to the superseded Compiler V2 ownership model."""

    def tracked(self) -> list[str]:
        return git(ROOT, "ls-files").splitlines()

    def test_one_compiler_authority(self) -> None:
        self.assertFalse((ROOT / "tools/l9_make").exists())
        templates = [path for path in self.tracked() if path.endswith(".template")]
        self.assertEqual(["tools/l9_repo/Makefile.template"], sorted(templates))

    def test_no_live_make_plan_or_second_compiler_reference(self) -> None:
        # Prose may name the superseded model in order to forbid it; code,
        # configuration, Make fragments, and workflows may not reference it.
        self_path = pathlib.Path(__file__).relative_to(ROOT).as_posix()
        offenders = []
        for relative in self.tracked():
            if relative in {self_path, "MANIFEST.sha256"} or relative.endswith(".md"):
                continue
            path = ROOT / relative
            if not path.is_file() or path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in ("l9.make-plan", "tools.l9_make", "tools/l9_make"):
                if marker in text:
                    offenders.append(f"{relative}: {marker}")
        self.assertEqual([], offenders)

    def test_repo_mk_is_repository_owned_not_generated(self) -> None:
        text = (ROOT / "Repo.mk").read_text(encoding="utf-8")
        self.assertNotIn("GENERATED", text)
        self.assertNotIn("DO NOT EDIT", text)
        self.assertIn("REPOSITORY-OWNED", text)
        self.assertFalse((ROOT / "Repo.local.mk").exists())
        self.assertNotIn("Repo.local.mk", TEMPLATE.read_text(encoding="utf-8"))

    def test_v2_contract_carries_no_commands(self) -> None:
        data = json.loads((ROOT / ".l9/repo-workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(v2_contract(), data)

    def test_generated_facade_has_no_product_commands(self) -> None:
        # Recipe lines start with "\t@"; `.PHONY` continuation lines are also
        # tab-indented but are target names, not commands.
        recipes = [
            line.strip()
            for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
            if line.startswith("\t@")
        ]
        routed = [line for line in recipes if not line.startswith("@awk")]
        self.assertTrue(routed)
        for line in routed:
            with self.subTest(line=line):
                self.assertRegex(line, r"^@\$\(L9\) [a-z-]+$")

    def test_reconcile_never_writes_repo_mk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            consumer(root)
            repo_mk = root / "Repo.mk"
            repo_mk.chmod(0o444)
            try:
                RepositoryWorkflow(root).reconcile()
            finally:
                repo_mk.chmod(0o644)


if __name__ == "__main__":
    unittest.main()
