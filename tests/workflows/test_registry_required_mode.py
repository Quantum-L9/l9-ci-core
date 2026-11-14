"""Registry-driven ``required`` mode and stamp drift in the admission bridge.

A repository the static projection does not list is ``required`` when the live
Quantum-L9/.github registry names it with ``lifecycle: current``; the registry
can only raise the mode, an unreadable registry fails closed, and stamp drift
blocks only in ``required`` mode.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
REGISTRY_FIXTURE = (
    ROOT / "tests" / "fixtures" / "repository-registry" / "repository_registry.yaml"
)
REVISION = "0123456789abcdef0123456789abcdef01234567"
BORN = "Quantum-L9/l9-born-node"

sys.path.insert(0, str(ROOT / "tools"))

import project_required_repositories  # noqa: E402
from l9_repo.stamp import stamp  # noqa: E402


def _load_runner():
    spec = importlib.util.spec_from_file_location("l9_registry_mode_bridge", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registry_with(name: str, lifecycle: str, *, extra: str = "") -> str:
    entry = (
        f"  - id: {name.lower()}\n"
        "    coordinate:\n"
        "      provider: github\n"
        "      organization: Quantum-L9\n"
        f"      repository: {name}\n"
        f"    lifecycle: {lifecycle}\n"
        "    class_ref: l9.repository-class/l9@1\n"
        f"{extra}"
    )
    text = REGISTRY_FIXTURE.read_text(encoding="utf-8")
    return text.replace("\naliases:", "\n" + entry + "aliases:", 1)


def write_v2_consumer(root: Path, *, profile: str | None = None) -> None:
    leaves = ["repo-setup", "repo-validate", "repo-check", "repo-test"]
    (root / "Repo.mk").write_text(
        ".PHONY: " + " ".join(leaves) + "\n" + " ".join(leaves) + ":\n\t@true\n",
        encoding="utf-8",
    )
    stamp(root, profile or "python", REVISION)


def run(runner, workspace: Path, repository: str) -> dict[str, str]:
    outputs: dict[str, str] = {}
    with tempfile.NamedTemporaryFile() as output:
        env = {
            "L9_REPOSITORY_WORKSPACE": str(workspace),
            "GITHUB_OUTPUT": output.name,
            "L9_REPOSITORY_IDENTITY": repository,
        }
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            assert runner.main() == 0
        for line in Path(output.name).read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs


class RegistryReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()

    def test_stdlib_reader_agrees_with_the_yaml_projection(self) -> None:
        text = REGISTRY_FIXTURE.read_text(encoding="utf-8")
        members = self.runner.parse_registry_members(text)
        current = sorted(
            (name for name, lifecycle in members if lifecycle == "current"),
            key=str.casefold,
        )
        self.assertEqual(
            project_required_repositories.project(yaml.safe_load(text)), current
        )

    def test_static_projection_matches_the_fixture(self) -> None:
        policy = json.loads(
            (RUNNER.parent / "contract-policy.json").read_text(encoding="utf-8")
        )
        text = REGISTRY_FIXTURE.read_text(encoding="utf-8")
        for name in policy["required_repositories"]:
            self.assertTrue(self.runner.registry_requires(name, text), name)

    def test_additional_entry_fields_are_skipped(self) -> None:
        text = registry_with(
            "l9-born-node",
            "current",
            extra="    birth:\n      profile: python\n      tags:\n        - node\n",
        )
        self.assertTrue(self.runner.registry_requires(BORN, text))
        self.assertTrue(self.runner.registry_requires(BORN.upper(), text))

    def test_unreadable_registries_fail_closed(self) -> None:
        text = REGISTRY_FIXTURE.read_text(encoding="utf-8")
        cases = {
            "empty": "",
            "wrong schema": text.replace(
                "schema: l9.repository-registry/v1", "schema: l9.repository-registry/v2"
            ),
            "not canonical": text.replace("canonical: true", "canonical: false"),
            "unknown lifecycle": registry_with("l9-born-node", "incubating"),
            "duplicate id": registry_with("l9-ci-core", "current"),
            "no class_ref": registry_with("l9-born-node", "current").replace(
                "    class_ref: l9.repository-class/l9@1\naliases:", "aliases:"
            ),
            "bad indentation": text.replace(
                "    lifecycle: current", "   lifecycle: current", 1
            ),
        }
        for name, document in cases.items():
            with self.subTest(case=name), self.assertRaises(self.runner.RegistryError):
                self.runner.parse_registry_members(document)


class EnforcementModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_registered_current_repository_is_required_without_policy_edit(
        self,
    ) -> None:
        self.runner.fetch_registry = lambda: registry_with("l9-born-node", "current")
        outputs = run(self.runner, self.root, BORN)
        self.assertEqual("required", outputs["contract-mode"])
        self.assertEqual("fail", outputs["status"])
        self.assertEqual("contract", outputs["failure-kind"])

    def test_retired_or_unregistered_repository_keeps_migration(self) -> None:
        for text in (
            registry_with("l9-born-node", "retired"),
            REGISTRY_FIXTURE.read_text(encoding="utf-8"),
        ):
            with self.subTest():
                self.runner.fetch_registry = lambda text=text: text
                outputs = run(self.runner, self.root, BORN)
                self.assertEqual("migration", outputs["contract-mode"])
                self.assertEqual("not_applicable", outputs["status"])

    def test_unreadable_registry_fails_closed_as_infrastructure(self) -> None:
        def unavailable() -> str:
            raise self.runner.RegistryError("network unavailable")

        self.runner.fetch_registry = unavailable
        outputs = run(self.runner, self.root, BORN)
        self.assertEqual("fail", outputs["status"])
        self.assertEqual("infrastructure", outputs["failure-kind"])
        self.assertEqual("unresolved", outputs["contract-mode"])

    def test_statically_required_repository_never_reads_the_registry(self) -> None:
        def forbidden() -> str:
            raise AssertionError("registry must not be read")

        self.runner.fetch_registry = forbidden
        outputs = run(self.runner, self.root, "Quantum-L9/l9-ci-core")
        self.assertEqual("required", outputs["contract-mode"])

    def test_consumer_files_cannot_lower_a_registry_required_mode(self) -> None:
        self.runner.fetch_registry = lambda: registry_with("l9-born-node", "current")
        (self.root / ".l9").mkdir()
        (self.root / ".l9" / "ci.json").write_text(
            json.dumps({"schema": "l9.ci-consumer/v1", "mode": "migration"})
        )
        (self.root / ".l9" / "contract-policy.json").write_text(
            json.dumps({"default_mode": "migration", "required_repositories": []})
        )
        outputs = run(self.runner, self.root, BORN)
        self.assertEqual("required", outputs["contract-mode"])


class StampDriftModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()
        self.runner.fetch_registry = lambda: registry_with("l9-born-node", "current")
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_v2_consumer(self.root)
        with (self.root / "ruff.toml").open("a", encoding="utf-8") as stream:
            stream.write("# local edit\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_drift_blocks_in_required_mode_before_any_phase(self) -> None:
        with unittest.mock.patch.object(self.runner, "run_v2") as phases:
            outputs = run(self.runner, self.root, BORN)
        phases.assert_not_called()
        self.assertEqual("required", outputs["contract-mode"])
        self.assertEqual("fail", outputs["status"])
        self.assertEqual("contract", outputs["failure-kind"])

    def test_drift_is_advisory_in_migration_mode(self) -> None:
        with unittest.mock.patch.object(self.runner, "run_v2") as phases:
            outputs = run(self.runner, self.root, "Quantum-L9/example-consumer")
        phases.assert_called_once()
        self.assertEqual("migration", outputs["contract-mode"])
        self.assertEqual("pass", outputs["status"])

    def test_aligned_stamp_passes_in_required_mode(self) -> None:
        stamp(self.root, "python", REVISION)
        with unittest.mock.patch.object(self.runner, "run_v2") as phases:
            outputs = run(self.runner, self.root, BORN)
        phases.assert_called_once()
        self.assertEqual("pass", outputs["status"])

    def test_malformed_record_fails_in_both_modes(self) -> None:
        (self.root / ".l9" / "core-stamp.json").write_text("{}", encoding="utf-8")
        for repository in (BORN, "Quantum-L9/example-consumer"):
            with (
                self.subTest(repository=repository),
                unittest.mock.patch.object(self.runner, "run_v2") as phases,
            ):
                outputs = run(self.runner, self.root, repository)
            phases.assert_not_called()
            self.assertEqual("fail", outputs["status"])
            self.assertEqual("contract", outputs["failure-kind"])


if __name__ == "__main__":
    unittest.main()
