"""Private-repository reads in Core actions (2026-11-17).

``Quantum-L9/l9-ci-sdk`` and ``Quantum-L9/.github`` went private on 2026-11-16,
so ``provision-sdk`` and the repository-verification registry read fail
anonymously in every governed repository. Both now accept the organization's
read token, and these tests hold the rules that keep it safe:

- the token reaches git only through GIT_CONFIG_* environment, never argv;
- no empty token turns into a header (public reads stay anonymous);
- ``provision-sdk`` pops the token at import, so no child inherits it;
- in repository verification only the registry snapshot and the composer
  prefetch hold the token, and the verify step, which runs the consumer's
  make targets, never does.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ROOT / ".github" / "actions"
TOKEN = "example-read-token-not-a-real-secret"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _header(env: dict[str, str]) -> str:
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    for index in range(count):
        if env[f"GIT_CONFIG_KEY_{index}"] == "http.https://github.com/.extraheader":
            return env[f"GIT_CONFIG_VALUE_{index}"]
    return ""


class ProvisionSdkAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"INPUT_GITHUB_READ_TOKEN": TOKEN}):
            self.module = _load(
                "provision_sdk_auth", ACTIONS / "provision-sdk" / "provision.py"
            )
            self.leftover = os.environ.get("INPUT_GITHUB_READ_TOKEN")

    def test_token_is_popped_from_the_process_environment_at_import(self) -> None:
        self.assertIsNone(self.leftover)
        self.assertEqual(self.module._READ_TOKEN, TOKEN)

    def test_token_becomes_a_basic_extraheader_for_github_only(self) -> None:
        env = self.module.github_auth_env(TOKEN, {})
        expected = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        self.assertEqual(_header(env), f"AUTHORIZATION: basic {expected}")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_an_existing_git_config_count_is_extended_not_overwritten(self) -> None:
        base = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "a.b",
            "GIT_CONFIG_VALUE_0": "c",
        }
        env = self.module.github_auth_env(TOKEN, base)
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "a.b")
        self.assertTrue(_header(env))

    def test_empty_token_adds_no_header(self) -> None:
        env = self.module.github_auth_env("", {})
        self.assertNotIn("GIT_CONFIG_COUNT", env)

    def test_fetch_passes_the_token_by_environment_never_argv(self) -> None:
        calls: list[tuple[list[str], dict[str, str] | None]] = []

        def fake_run(command, *, cwd=None, capture=False, env=None):
            calls.append((command, env))
            if "fetch" in command:
                raise self.module.ProvisioningError("stop after fetch")

        with (
            tempfile.TemporaryDirectory() as tmp,
            unittest.mock.patch.object(self.module, "run", side_effect=fake_run),
            unittest.mock.patch.object(self.module, "_READ_TOKEN", TOKEN),
        ):
            with self.assertRaises(self.module.ProvisioningError):
                self.module.checkout_sdk(
                    self.module.EXPECTED_REPOSITORY, "a" * 40, Path(tmp) / "sdk"
                )
        ((command, env),) = [(c, e) for c, e in calls if "fetch" in c]
        self.assertNotIn(TOKEN, " ".join(command))
        assert env is not None
        self.assertIn("basic", _header(env))

    def test_action_maps_the_input_to_the_popped_variable(self) -> None:
        action = yaml.safe_load((ACTIONS / "provision-sdk" / "action.yml").read_text())
        self.assertEqual(action["inputs"]["github-read-token"]["default"], "")
        step = action["runs"]["steps"][-1]
        self.assertEqual(
            step["env"]["INPUT_GITHUB_READ_TOKEN"], "${{ inputs.github-read-token }}"
        )


class RepositoryVerificationAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load(
            "repo_verify_auth", ACTIONS / "run-repository-verification" / "run.py"
        )

    def test_only_the_pre_consumer_steps_hold_the_token(self) -> None:
        action = yaml.safe_load(
            (ACTIONS / "run-repository-verification" / "action.yml").read_text()
        )
        steps = {step["id"]: step for step in action["runs"]["steps"]}
        ids = [step["id"] for step in action["runs"]["steps"]]
        self.assertLess(ids.index("registry"), ids.index("composer"))
        self.assertLess(ids.index("composer"), ids.index("external"))
        self.assertLess(ids.index("external"), ids.index("verify"))
        for step_id in ("registry", "composer", "external"):
            self.assertIn(
                "github-read-token", steps[step_id]["env"]["L9_GITHUB_READ_TOKEN"]
            )
        self.assertNotIn("L9_GITHUB_READ_TOKEN", steps["verify"]["env"])
        self.assertNotIn("github-read-token", yaml.safe_dump(steps["verify"]))
        self.assertIn("SEED_COMPOSER_ROOT", steps["verify"]["env"])
        self.assertIn(
            "steps.composer.outputs.seed-root",
            steps["verify"]["env"]["SEED_COMPOSER_ROOT"],
        )

    def test_verify_reads_the_snapshot_instead_of_the_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "registry.yaml"
            snap.write_text("snapshot-bytes", encoding="utf-8")
            with (
                unittest.mock.patch.dict(
                    os.environ, {self.module.REGISTRY_SNAPSHOT_ENV: str(snap)}
                ),
                unittest.mock.patch.object(
                    self.module,
                    "fetch_live_registry",
                    side_effect=AssertionError("network"),
                ),
            ):
                self.assertEqual(self.module.fetch_registry(), "snapshot-bytes")

    def test_missing_snapshot_falls_back_to_the_live_read(self) -> None:
        with (
            unittest.mock.patch.dict(
                os.environ,
                {self.module.REGISTRY_SNAPSHOT_ENV: "/nonexistent/registry.yaml"},
            ),
            unittest.mock.patch.object(
                self.module, "fetch_live_registry", return_value="live"
            ) as live,
        ):
            self.assertEqual(self.module.fetch_registry(), "live")
        live.assert_called_once()

    def test_failed_snapshot_removes_the_file_and_never_fails_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "registry.yaml"
            snap.write_text("stale", encoding="utf-8")
            with unittest.mock.patch.object(
                self.module,
                "fetch_live_registry",
                side_effect=self.module.RegistryError("HTTP 403"),
            ):
                self.assertEqual(self.module.snapshot_registry(snap), 0)
            self.assertFalse(snap.exists())

    def test_snapshot_consumes_the_token_from_the_environment(self) -> None:
        seen: list[str] = []
        fixture = (
            ROOT
            / "tests"
            / "fixtures"
            / "repository-registry"
            / "repository_registry.yaml"
        ).read_text(encoding="utf-8")
        with (
            tempfile.TemporaryDirectory() as tmp,
            unittest.mock.patch.dict(os.environ, {self.module.READ_TOKEN_ENV: TOKEN}),
            unittest.mock.patch.object(
                self.module,
                "fetch_live_registry",
                side_effect=lambda t="": seen.append(t) or fixture,
            ),
        ):
            snap = Path(tmp) / "registry.yaml"
            self.assertEqual(self.module.snapshot_registry(snap), 0)
            self.assertEqual(snap.read_text(encoding="utf-8"), fixture)
            self.assertNotIn(self.module.READ_TOKEN_ENV, os.environ)
        self.assertEqual(seen, [TOKEN])

    def _output(self, directory: str) -> str:
        return (Path(directory) / "github-output.txt").read_text(encoding="utf-8")

    def test_absent_workflow_does_not_fetch(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            unittest.mock.patch.dict(
                os.environ,
                {
                    self.module.READ_TOKEN_ENV: TOKEN,
                    "GITHUB_OUTPUT": str(Path(tmp) / "github-output.txt"),
                },
            ),
            unittest.mock.patch.object(
                self.module, "fetch_composer", side_effect=AssertionError("fetch")
            ),
        ):
            self.assertEqual(
                self.module.prefetch_composer(Path(tmp) / "ws", Path(tmp) / "dest"), 0
            )
            self.assertNotIn(self.module.READ_TOKEN_ENV, os.environ)
            self.assertIn("seed-root=\n", self._output(tmp))

    def test_workflow_without_a_pin_does_not_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workflow = workspace / self.module.COMPOSER_WORKFLOW
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: Governance\non: workflow_call:\n", encoding="utf-8"
            )
            with (
                unittest.mock.patch.dict(
                    os.environ,
                    {
                        self.module.READ_TOKEN_ENV: TOKEN,
                        "GITHUB_OUTPUT": str(Path(tmp) / "github-output.txt"),
                    },
                ),
                unittest.mock.patch.object(
                    self.module, "fetch_composer", side_effect=AssertionError("fetch")
                ),
            ):
                self.assertEqual(
                    self.module.prefetch_composer(workspace, Path(tmp) / "dest"), 0
                )
                self.assertNotIn(self.module.READ_TOKEN_ENV, os.environ)
            self.assertIn("seed-root=\n", self._output(tmp))

    def test_one_pin_fetches_only_the_two_files_and_drops_the_token(self) -> None:
        pin = "a" * 40
        blobs = {
            "ops/scripts/compose_pr_body.py": b"print('body')\n",
            "ops/scripts/org_dotgithub.py": b"VALUE = 1\n",
        }
        commands: list[tuple[list[str], dict[str, str] | None]] = []

        def fake_run(argv, **kwargs):
            args = argv[3:]
            stdout = b""
            if args and args[0] == "show":
                stdout = blobs[args[1].split(":", 1)[1]]
            commands.append((list(argv), kwargs.get("env")))
            return subprocess.CompletedProcess(argv, 0, stdout, b"")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workflow = workspace / self.module.COMPOSER_WORKFLOW
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                f"env:\n  COMPOSER: Quantum-L9/Cursor-Governance@{pin}\n",
                encoding="utf-8",
            )
            destination = Path(tmp) / "dest"
            with (
                unittest.mock.patch.dict(
                    os.environ,
                    {
                        self.module.READ_TOKEN_ENV: TOKEN,
                        "GITHUB_OUTPUT": str(Path(tmp) / "github-output.txt"),
                    },
                ),
                unittest.mock.patch.object(
                    self.module.subprocess, "run", side_effect=fake_run
                ),
            ):
                self.assertEqual(
                    self.module.prefetch_composer(workspace, destination), 0
                )
                self.assertNotIn(self.module.READ_TOKEN_ENV, os.environ)
            for relative, body in blobs.items():
                self.assertEqual((destination / relative).read_bytes(), body)
            self.assertEqual(
                (destination / ".pin").read_text(encoding="utf-8"), f"{pin}\n"
            )
            self.assertIn(f"seed-root={destination}\n", self._output(tmp))
        fetched = [argv for argv, _env in commands if "fetch" in argv]
        self.assertEqual(len(fetched), 1)
        self.assertNotIn(TOKEN, " ".join(fetched[0]))
        self.assertIn(pin, fetched[0])
        self.assertIn(self.module.COMPOSER_REPOSITORY, fetched[0])
        fetch_env = next(env for argv, env in commands if "fetch" in argv)
        assert fetch_env is not None
        self.assertNotIn(self.module.READ_TOKEN_ENV, fetch_env)
        self.assertIn("basic", _header(fetch_env))
        shown = [
            argv[argv.index("show") + 1] for argv, _env in commands if "show" in argv
        ]
        self.assertEqual(
            shown,
            [f"FETCH_HEAD:{relative}" for relative in self.module.COMPOSER_FILES],
        )

    def test_two_pins_fail_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workflow = workspace / self.module.COMPOSER_WORKFLOW
            workflow.parent.mkdir(parents=True)
            line = "COMPOSER: Quantum-L9/Cursor-Governance@" + ("b" * 40)
            workflow.write_text(f"{line}\n{line}\n", encoding="utf-8")
            with (
                unittest.mock.patch.dict(
                    os.environ,
                    {
                        self.module.READ_TOKEN_ENV: TOKEN,
                        "GITHUB_OUTPUT": str(Path(tmp) / "github-output.txt"),
                    },
                ),
                unittest.mock.patch.object(
                    self.module, "fetch_composer", side_effect=AssertionError("fetch")
                ),
            ):
                self.assertEqual(
                    self.module.prefetch_composer(workspace, Path(tmp) / "dest"), 1
                )
                self.assertNotIn(self.module.READ_TOKEN_ENV, os.environ)

    def test_a_failed_fetch_of_a_declared_pin_fails_the_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workflow = workspace / self.module.COMPOSER_WORKFLOW
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "COMPOSER: Quantum-L9/Cursor-Governance@" + ("c" * 40) + "\n",
                encoding="utf-8",
            )
            with (
                unittest.mock.patch.dict(
                    os.environ, {self.module.READ_TOKEN_ENV: TOKEN}
                ),
                unittest.mock.patch.object(
                    self.module,
                    "fetch_composer",
                    side_effect=self.module.ComposerError("could not read"),
                ),
            ):
                self.assertEqual(
                    self.module.prefetch_composer(workspace, Path(tmp) / "dest"), 1
                )
                self.assertNotIn(self.module.READ_TOKEN_ENV, os.environ)

    def test_git_auth_env_never_forwards_the_raw_token_variable(self) -> None:
        with unittest.mock.patch.dict(os.environ, {self.module.READ_TOKEN_ENV: TOKEN}):
            env = self.module.github_auth_env(TOKEN)
        self.assertNotIn(self.module.READ_TOKEN_ENV, env)
        self.assertTrue(_header(env))


if __name__ == "__main__":
    unittest.main()
