"""The bounded V1 path the admission bridge still delegates to.

``RepositoryWorkflow`` exists so a repository that still declares
``schema_version: 1`` keeps executing through the pinned bridge. It runs that
contract's ``commands`` matrices argv-only under the executable allowlist and
nothing more.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import PHASES, ROOT, commit_all, init_repo, v2_contract  # noqa: E402

from l9_repo.__main__ import RepositoryWorkflow, WorkflowError  # noqa: E402

BRIDGE = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"


def log_command(name: str) -> list[str]:
    return ["@python", "-c", f"open({name + '.log'!r}, 'a').write({name!r})"]


def v1_contract(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "commands": {phase: [log_command(phase)] for phase in PHASES},
        "clean_paths": [".cache"],
    }
    document.update(overrides)
    return document


def write_v1_consumer(root: pathlib.Path, document: object) -> None:
    (root / ".l9").mkdir(parents=True, exist_ok=True)
    (root / ".l9/repo-workflow.json").write_text(json.dumps(document), encoding="utf-8")
    init_repo(root)
    commit_all(root, "v1 consumer")


class LegacyExecutionTests(unittest.TestCase):
    def test_each_phase_runs_its_declared_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v1_consumer(root, v1_contract())
            workflow = RepositoryWorkflow(root)
            workflow.setup()
            workflow.validate()
            workflow.check()
            workflow.test()
            for phase in PHASES:
                self.assertEqual(
                    phase, (root / f"{phase}.log").read_text(encoding="utf-8")
                )

    def test_multi_command_matrix_runs_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            commands = {
                **{phase: [log_command(phase)] for phase in PHASES},
                "check": [
                    ["@python", "-c", "open('order.log','a').write('a')"],
                    ["@python", "-c", "open('order.log','a').write('b')"],
                ],
            }
            write_v1_consumer(root, v1_contract(commands=commands))
            RepositoryWorkflow(root).check()
            self.assertEqual("ab", (root / "order.log").read_text(encoding="utf-8"))

    def test_unallowlisted_executable_is_rejected_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            commands = {
                **{phase: [log_command(phase)] for phase in PHASES},
                "check": [["curl", "https://example.invalid"]],
            }
            write_v1_consumer(root, v1_contract(commands=commands))
            with self.assertRaisesRegex(WorkflowError, "argv-only allowlist"):
                RepositoryWorkflow(root).check()

    def test_missing_phase_matrix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            commands = {
                phase: [log_command(phase)] for phase in PHASES if phase != "test"
            }
            write_v1_consumer(root, v1_contract(commands=commands))
            with self.assertRaisesRegex(WorkflowError, "commands.test"):
                RepositoryWorkflow(root).test()

    def test_v2_or_unknown_contracts_are_refused_by_the_legacy_path(self) -> None:
        for label, document in (
            ("v2", v2_contract()),
            ("unknown", {"schema_version": 2}),
            ("bool", {"schema_version": True}),
        ):
            with self.subTest(contract=label), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                write_v1_consumer(root, document)
                with self.assertRaisesRegex(WorkflowError, "schema_version 1"):
                    RepositoryWorkflow(root).setup()

    def test_workspace_must_be_a_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v1_consumer(root, v1_contract())
            nested = root / "nested"
            nested.mkdir()
            (nested / ".l9").mkdir()
            (nested / ".l9/repo-workflow.json").write_text(
                json.dumps(v1_contract()), encoding="utf-8"
            )
            with self.assertRaisesRegex(WorkflowError, "not repository root"):
                RepositoryWorkflow(nested).setup()


class BridgeLegacyEndToEndTests(unittest.TestCase):
    """The pinned admission bridge still passes a real V1 consumer on main."""

    def run_bridge(self, root: pathlib.Path) -> dict[str, str]:
        spec = importlib.util.spec_from_file_location("l9_bridge_for_v1", BRIDGE)
        assert spec is not None and spec.loader is not None
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        outputs: dict[str, str] = {}
        with tempfile.NamedTemporaryFile() as output:
            env = {"L9_REPOSITORY_WORKSPACE": str(root), "GITHUB_OUTPUT": output.name}
            with unittest.mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(0, bridge.main())
            for line in (
                pathlib.Path(output.name).read_text(encoding="utf-8").splitlines()
            ):
                key, _, value = line.partition("=")
                outputs[key] = value
        return outputs

    def test_v1_consumer_passes_through_the_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_v1_consumer(root, v1_contract())
            self.assertEqual(
                {"present": "true", "status": "pass"}, self.run_bridge(root)
            )
            for phase in PHASES:
                self.assertTrue((root / f"{phase}.log").is_file(), phase)

    def test_v1_consumer_failure_is_recorded_through_the_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            commands = {
                **{phase: [log_command(phase)] for phase in PHASES},
                "check": [["@python", "-c", "raise SystemExit(1)"]],
            }
            write_v1_consumer(root, v1_contract(commands=commands))
            self.assertEqual(
                {"present": "true", "status": "fail"}, self.run_bridge(root)
            )
            self.assertFalse((root / "test.log").exists())


if __name__ == "__main__":
    unittest.main()
