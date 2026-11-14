"""The release engine's contract declaration and composite-action wiring."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from support import ROOT  # noqa: E402

from l9_release.config import REFUSED_REPOSITORIES  # noqa: E402

ACTION = ROOT / ".github" / "actions" / "release-engine"
SHA = "c" * 40


def load_entrypoint():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "release_engine_run", ACTION / "release_engine.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleasePlaneDeclarationTests(unittest.TestCase):
    def setUp(self) -> None:
        plane = yaml.safe_load((ROOT / ".l9" / "release-plane.yaml").read_text())
        self.declaration = plane["consumer_release_engine"]

    def test_declared_paths_exist(self) -> None:
        for key in ("engine", "action", "runbook"):
            with self.subTest(key=key):
                self.assertTrue((ROOT / self.declaration[key]).exists())

    def test_core_is_refused_by_contract_and_code(self) -> None:
        self.assertEqual(
            REFUSED_REPOSITORIES,
            {name.lower() for name in self.declaration["refused_repositories"]},
        )
        self.assertIn("quantum-l9/l9-ci-core", REFUSED_REPOSITORIES)

    def test_no_registry_publication(self) -> None:
        self.assertEqual("none", self.declaration["registry_publication"])
        engine = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tools" / "l9_release").glob("*.py")
        )
        for token in ("npm publish", "twine", "uv publish", '"publish"]'):
            with self.subTest(token=token):
                self.assertNotIn(token, engine)


class ActionEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entry = load_entrypoint()

    def test_action_has_no_target_selecting_input(self) -> None:
        action = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))
        self.assertEqual(
            {"stage", "workspace", "dist", "phases"}, set(action["inputs"])
        )
        env = action["runs"]["steps"][0]["env"]
        self.assertEqual("${{ github.sha }}", env["L9_RELEASE_COMMIT"])

    def test_stage_arguments(self) -> None:
        base = {"L9_RELEASE_COMMIT": SHA, "RUNNER_TEMP": "/rt"}
        self.assertEqual(
            ["--workspace", ".", "--commit", SHA, "plan"],
            self.entry.arguments({**base, "L9_RELEASE_STAGE": "plan"}),
        )
        self.assertEqual(
            [
                "--workspace",
                ".",
                "--commit",
                SHA,
                "build",
                "--out",
                "d",
                "--scratch",
                "/rt/l9-release-scratch",
                "--phases",
                "verified-upstream",
            ],
            self.entry.arguments(
                {
                    **base,
                    "L9_RELEASE_STAGE": "build",
                    "L9_RELEASE_DIST": "d",
                    "L9_RELEASE_PHASES": "verified-upstream",
                }
            ),
        )
        self.assertEqual(
            ["--workspace", ".", "--commit", SHA, "publish", "--dist", "d"],
            self.entry.arguments(
                {**base, "L9_RELEASE_STAGE": "publish", "L9_RELEASE_DIST": "d"}
            ),
        )

    def test_entrypoint_fails_closed(self) -> None:
        for env in (
            {"L9_RELEASE_STAGE": "deploy", "L9_RELEASE_COMMIT": SHA},
            {"L9_RELEASE_STAGE": "plan", "L9_RELEASE_COMMIT": "main"},
            {"L9_RELEASE_STAGE": "build", "L9_RELEASE_COMMIT": SHA},
            {"L9_RELEASE_STAGE": "publish", "L9_RELEASE_COMMIT": SHA},
        ):
            with self.subTest(env=env), self.assertRaises(SystemExit):
                self.entry.arguments(env)


if __name__ == "__main__":
    unittest.main()
