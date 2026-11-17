"""Private-repository reads in Core actions (2026-11-17).

``Quantum-L9/l9-ci-sdk`` and ``Quantum-L9/.github`` went private on 2026-11-16,
so ``provision-sdk`` and the repository-verification registry read fail
anonymously in every governed repository. Both now accept the organization's
read token, and these tests hold the rules that keep it safe:

- the token reaches git only through GIT_CONFIG_* environment, never argv;
- no empty token turns into a header (public reads stay anonymous);
- ``provision-sdk`` pops the token at import, so no child inherits it;
- in repository verification only the snapshot step holds the token, and the
  verify step, which runs the consumer's make targets, never does.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ROOT / ".github" / "actions"
TOKEN = "ghs_exampleexampleexampleexample0000"


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

    def test_only_the_snapshot_step_holds_the_token(self) -> None:
        action = yaml.safe_load(
            (ACTIONS / "run-repository-verification" / "action.yml").read_text()
        )
        steps = {step["id"]: step for step in action["runs"]["steps"]}
        ids = [step["id"] for step in action["runs"]["steps"]]
        self.assertLess(ids.index("registry"), ids.index("verify"))
        self.assertIn(
            "github-read-token", steps["registry"]["env"]["L9_GITHUB_READ_TOKEN"]
        )
        self.assertNotIn("L9_GITHUB_READ_TOKEN", steps["verify"]["env"])
        self.assertNotIn("github-read-token", yaml.safe_dump(steps["verify"]))

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

    def test_git_auth_env_never_forwards_the_raw_token_variable(self) -> None:
        with unittest.mock.patch.dict(os.environ, {self.module.READ_TOKEN_ENV: TOKEN}):
            env = self.module.github_auth_env(TOKEN)
        self.assertNotIn(self.module.READ_TOKEN_ENV, env)
        self.assertTrue(_header(env))


if __name__ == "__main__":
    unittest.main()
