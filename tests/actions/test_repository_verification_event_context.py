"""Repository verification can run a consumer under push semantics (row 27).

The promotion canary checks out a consumer's main SHA inside a Core
pull_request run. The runner's GITHUB_* describe that Core PR, and a workflow
``env:`` cannot overwrite them, so ``run.py`` sets the context the consumer's
make inherits. A consumer that scopes ``make test`` by event (Cursor-Governance
refuses ``pull_request`` on a non-merge HEAD) must see ``push`` and the commit
it actually checked out, never the Core PR's event.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "run-repository-verification"
RUNNER = ACTION / "run.py"
REGISTRY_FIXTURE = (
    ROOT / "tests" / "fixtures" / "repository-registry" / "repository_registry.yaml"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("l9_verify_event", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fetch_registry = REGISTRY_FIXTURE.read_text
    return module


runner = _load_runner()

CORE_PR = {
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_SHA": "f" * 40,
    "GITHUB_REF": "refs/pull/325/merge",
    "GITHUB_BASE_REF": "main",
    "GITHUB_HEAD_REF": "feature",
    "GITHUB_EVENT_PATH": "/core/pull_request/event.json",
    "GITHUB_REPOSITORY": "Quantum-L9/l9-ci-core",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repository(root: Path, commits: int = 2) -> list[str]:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    shas = []
    for number in range(commits):
        (root / "file.txt").write_text(f"{number}\n", encoding="utf-8")
        git(root, "add", "file.txt")
        git(root, "commit", "-q", "-m", f"commit {number}")
        shas.append(git(root, "rev-parse", "HEAD"))
    return shas


class ApplyEventContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "consumer"
        self.root.mkdir()
        self.temp = Path(self.directory.name) / "runner-temp"
        self.temp.mkdir()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def environ(self, **extra: str) -> dict[str, str]:
        return {**CORE_PR, "RUNNER_TEMP": str(self.temp), **extra}

    def test_no_event_leaves_the_runs_own_context(self) -> None:
        repository(self.root)
        environ = self.environ()
        self.assertEqual(
            "", runner.apply_event_context(self.root, "Quantum-L9/x", environ)
        )
        self.assertEqual(self.environ(), environ)

    def test_push_replaces_the_core_pull_request_context(self) -> None:
        before, head = repository(self.root)
        environ = self.environ(
            L9_VERIFICATION_EVENT="push", L9_VERIFICATION_REF="refs/heads/main"
        )
        event = runner.apply_event_context(
            self.root, "Quantum-L9/Cursor-Governance", environ
        )
        self.assertEqual("push", event)
        self.assertEqual("push", environ["GITHUB_EVENT_NAME"])
        self.assertEqual(head, environ["GITHUB_SHA"])
        self.assertEqual("refs/heads/main", environ["GITHUB_REF"])
        self.assertEqual("main", environ["GITHUB_REF_NAME"])
        self.assertEqual("branch", environ["GITHUB_REF_TYPE"])
        self.assertEqual("", environ["GITHUB_BASE_REF"])
        self.assertEqual("", environ["GITHUB_HEAD_REF"])
        self.assertEqual("Quantum-L9/Cursor-Governance", environ["GITHUB_REPOSITORY"])
        self.assertNotIn("L9_VERIFICATION_EVENT", environ)
        self.assertNotIn("L9_VERIFICATION_REF", environ)
        path = Path(environ["GITHUB_EVENT_PATH"])
        self.assertEqual(self.temp, path.parent)
        self.assertEqual(
            {
                "ref": "refs/heads/main",
                "before": before,
                "after": head,
                "repository": {"full_name": "Quantum-L9/Cursor-Governance"},
            },
            json.loads(path.read_text(encoding="utf-8")),
        )

    def test_a_root_commit_pushes_from_the_zero_sha(self) -> None:
        (head,) = repository(self.root, commits=1)
        environ = self.environ(
            L9_VERIFICATION_EVENT="push", L9_VERIFICATION_REF="refs/heads/main"
        )
        runner.apply_event_context(self.root, "Quantum-L9/x", environ)
        payload = json.loads(Path(environ["GITHUB_EVENT_PATH"]).read_text())
        self.assertEqual(("0" * 40, head), (payload["before"], payload["after"]))

    def test_refusals(self) -> None:
        repository(self.root)
        cases = {
            "pull_request event": {
                "L9_VERIFICATION_EVENT": "pull_request",
                "L9_VERIFICATION_REF": "refs/heads/main",
            },
            "push without ref": {"L9_VERIFICATION_EVENT": "push"},
            "tag ref": {
                "L9_VERIFICATION_EVENT": "push",
                "L9_VERIFICATION_REF": "refs/tags/v1",
            },
            "dotted ref": {
                "L9_VERIFICATION_EVENT": "push",
                "L9_VERIFICATION_REF": "refs/heads/a..b",
            },
            "ref without event": {"L9_VERIFICATION_REF": "refs/heads/main"},
        }
        for name, extra in cases.items():
            with self.subTest(name):
                environ = self.environ(**extra)
                with self.assertRaises(runner.EventContextError):
                    runner.apply_event_context(self.root, "Quantum-L9/x", environ)
                self.assertEqual("pull_request", environ["GITHUB_EVENT_NAME"])

    def test_a_workspace_without_a_commit_is_refused(self) -> None:
        environ = self.environ(
            L9_VERIFICATION_EVENT="push", L9_VERIFICATION_REF="refs/heads/main"
        )
        with self.assertRaises(runner.EventContextError):
            runner.apply_event_context(self.root, "Quantum-L9/x", environ)


class ConsumerCommandsSeeTheContextTests(unittest.TestCase):
    """End to end: the consumer's make phases inherit push, not the Core PR."""

    def test_make_phases_run_as_a_push_of_the_checked_out_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "consumer"
            root.mkdir()
            _, head = repository(root)
            output = Path(directory) / "output"
            seen = Path(directory) / "seen.txt"
            calls: list[str] = []

            real_run = subprocess.run

            def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
                if argv[0] != "make":
                    return real_run(argv, **kwargs)
                calls.append(" ".join(argv))
                with seen.open("a", encoding="utf-8") as stream:
                    stream.write(
                        f"{os.environ['GITHUB_EVENT_NAME']} {os.environ['GITHUB_SHA']}\n"
                    )
                return subprocess.CompletedProcess(argv, 0)

            env = {
                **CORE_PR,
                "RUNNER_TEMP": directory,
                "GITHUB_OUTPUT": str(output),
                "L9_REPOSITORY_WORKSPACE": str(root),
                "L9_REPOSITORY_IDENTITY": "Quantum-L9/Cursor-Governance",
                "L9_VERIFICATION_EVENT": "push",
                "L9_VERIFICATION_REF": "refs/heads/main",
            }
            with (
                unittest.mock.patch.dict(os.environ, env, clear=False),
                unittest.mock.patch.object(
                    runner, "resolve_enforcement", return_value="migration"
                ),
                unittest.mock.patch.object(
                    runner, "classify_contract", return_value=runner.CONTRACT_V2
                ),
                unittest.mock.patch.object(runner, "load_contract", return_value={}),
                unittest.mock.patch.object(runner, "verify_v2", return_value=None),
                unittest.mock.patch.object(
                    runner, "materialize_inherited", return_value=None
                ),
                unittest.mock.patch.object(runner.subprocess, "run", fake_run),
            ):
                (root / ".l9").mkdir()
                (root / ".l9" / "repo-workflow.json").write_text("{}", encoding="utf-8")
                self.assertEqual(0, runner.main())
            self.assertEqual(
                [f"make {phase}" for phase in runner.V2_REQUIRED_PHASES], calls
            )
            lines = seen.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(runner.V2_REQUIRED_PHASES), len(lines))
            self.assertEqual({f"push {head}"}, set(lines))
            self.assertIn("status=pass", output.read_text(encoding="utf-8"))

    def test_an_unsupported_event_fails_before_any_consumer_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "consumer"
            root.mkdir()
            repository(root)
            output = Path(directory) / "output"
            (root / ".l9").mkdir()
            (root / ".l9" / "repo-workflow.json").write_text("{}", encoding="utf-8")
            env = {
                **CORE_PR,
                "RUNNER_TEMP": directory,
                "GITHUB_OUTPUT": str(output),
                "L9_REPOSITORY_WORKSPACE": str(root),
                "L9_REPOSITORY_IDENTITY": "Quantum-L9/Cursor-Governance",
                "L9_VERIFICATION_EVENT": "pull_request",
                "L9_VERIFICATION_REF": "refs/heads/main",
            }
            ran = unittest.mock.Mock()
            with (
                unittest.mock.patch.dict(os.environ, env, clear=False),
                unittest.mock.patch.object(
                    runner, "resolve_enforcement", return_value="migration"
                ),
                unittest.mock.patch.object(runner, "run_v2", ran),
                unittest.mock.patch.object(runner, "run_v1", ran),
            ):
                self.assertEqual(0, runner.main())
            ran.assert_not_called()
            recorded = output.read_text(encoding="utf-8")
            self.assertIn("status=fail", recorded)
            self.assertIn("failure-kind=infrastructure", recorded)


class ActionWiringTests(unittest.TestCase):
    def test_the_verify_step_alone_receives_the_event_inputs(self) -> None:
        text = (ACTION / "action.yml").read_text(encoding="utf-8")
        self.assertIn("  event-name:\n", text)
        self.assertIn("  event-ref:\n", text)
        self.assertEqual(
            1, text.count("L9_VERIFICATION_EVENT: ${{ inputs.event-name }}")
        )
        self.assertEqual(1, text.count("L9_VERIFICATION_REF: ${{ inputs.event-ref }}"))
        verify = text[text.index("    - id: verify\n") :]
        self.assertIn("L9_VERIFICATION_EVENT", verify)


if __name__ == "__main__":
    unittest.main()
