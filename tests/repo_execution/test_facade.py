"""The make-v1 facade: tiny, deterministic, and free of publication.

The primary proof of the publication boundary is the target surface itself:
the facade declares exactly ``help`` plus the four phases. The substring
checks are regression tripwires on top of that.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import (  # noqa: E402
    PHASES,
    git,
    logging_repo_mk,
    make,
    phases_log,
    write_consumer,
)

from l9_repo.contract import (  # noqa: E402
    FACADE_TARGETS,
    ContractError,
    RepositoryExecution,
    make_phony_targets,
    make_target_declarations,
    render_facade,
)

FORBIDDEN_TOKENS = (
    "L9",
    "l9 pr",
    "git push",
    "gh pr",
    "pr:",
    "push:",
    "publish:",
    "release:",
    "deploy:",
    "start:",
    "workspace-clean:",
    "wiring-check:",
)


class FacadeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = render_facade("make-v1").decode("utf-8")

    def test_facade_declares_exactly_help_and_the_four_phases(self) -> None:
        declared = [target for _, target in make_target_declarations(self.text)]
        self.assertEqual(["help", *PHASES], declared)
        self.assertEqual(set(declared), FACADE_TARGETS)
        self.assertEqual(FACADE_TARGETS, make_phony_targets(self.text))

    def test_each_phase_is_exactly_one_repository_leaf(self) -> None:
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertRegex(self.text, rf"(?m)^{phase}: repo-{phase}( ##.*)?$")

    def test_facade_includes_repo_mk_by_composition_only(self) -> None:
        self.assertRegex(self.text, r"(?m)^include Repo\.mk$")
        self.assertNotIn("-include", self.text)
        self.assertNotIn("$(MAKE)", self.text)
        self.assertNotIn("-f Repo.mk", self.text)

    def test_facade_names_its_own_version(self) -> None:
        self.assertIn("make-v1", self.text.splitlines()[0])

    def test_facade_contains_no_publication_or_governance_vocabulary(self) -> None:
        for token in FORBIDDEN_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)

    def test_facade_recipes_run_nothing_but_help(self) -> None:
        recipes = [line for line in self.text.splitlines() if line.startswith("\t")]
        self.assertTrue(recipes)
        self.assertTrue(recipes[0].lstrip("\t").startswith("@awk"))
        # Every recipe line belongs to the awk help pipeline (continuations).
        self.assertTrue(all(line.rstrip().endswith("\\") for line in recipes[:-1]))


class FacadeDeterminismTests(unittest.TestCase):
    def test_rendering_is_deterministic(self) -> None:
        self.assertEqual(render_facade("make-v1"), render_facade("make-v1"))

    def test_unsupported_facade_cannot_render(self) -> None:
        with self.assertRaisesRegex(ContractError, "unsupported facade"):
            render_facade("make-v2")

    def test_reconcile_is_one_directional_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            (root / "Makefile").write_text("drift\n", encoding="utf-8")
            repo_mk_before = (root / "Repo.mk").read_bytes()
            execution = RepositoryExecution(root)
            self.assertTrue(execution.reconcile())
            self.assertEqual("", git(root, "status", "--porcelain"))
            self.assertFalse(execution.reconcile())
            self.assertEqual("", git(root, "status", "--porcelain"))
            self.assertEqual(render_facade("make-v1"), (root / "Makefile").read_bytes())
            self.assertEqual(repo_mk_before, (root / "Repo.mk").read_bytes())

    def test_reconcile_never_writes_repo_mk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            (root / "Makefile").unlink()
            repo_mk = root / "Repo.mk"
            repo_mk.chmod(0o444)
            try:
                RepositoryExecution(root).reconcile()
            finally:
                repo_mk.chmod(0o644)
            self.assertEqual(logging_repo_mk(), repo_mk.read_text(encoding="utf-8"))

    def test_drift_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            RepositoryExecution(root).validate()
            with (root / "Makefile").open("a", encoding="utf-8") as stream:
                stream.write("\nextra:\n\t@true\n")
            with self.assertRaisesRegex(ContractError, "drift"):
                RepositoryExecution(root).validate()
            (root / "Makefile").unlink()
            with self.assertRaisesRegex(ContractError, "missing generated"):
                RepositoryExecution(root).validate()


@unittest.skipUnless(shutil.which("make"), "GNU make is not installed")
class FacadeRoutingTests(unittest.TestCase):
    """``make <phase>`` reaches ``repo-<phase>`` through a real GNU make."""

    def test_each_phase_reaches_its_repository_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            for phase in PHASES:
                with self.subTest(phase=phase):
                    result = make(root, phase)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(phase, phases_log(root)[-1])
            self.assertEqual(list(PHASES), phases_log(root))

    def test_phase_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root, repo_mk=logging_repo_mk(failing="check"))
            result = make(root, "check")
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(["check"], phases_log(root))

    def test_same_named_file_cannot_suppress_a_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            for name in ("test", "repo-test"):
                (root / name).write_text("decoy\n", encoding="utf-8")
            result = make(root, "test")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["test"], phases_log(root))

    def test_help_lists_only_the_portable_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write_consumer(root)
            result = make(root, "help")
            self.assertEqual(0, result.returncode, result.stderr)
            listed = [line.split()[0] for line in result.stdout.splitlines() if line]
            self.assertEqual(["help", *PHASES], listed)

    def test_consumer_needs_no_core_runtime_or_dispatcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = pathlib.Path(tmp)
            root = sandbox / "consumer"
            root.mkdir()
            write_consumer(root)
            self.assertEqual(
                {".git", ".l9", "Makefile", "Repo.mk"},
                {path.name for path in root.iterdir()},
            )
            bin_dir = sandbox / "bin"
            bin_dir.mkdir()
            trap = sandbox / "trap.log"
            for tool in ("git", "gh", "l9", "python3", "python"):
                script = bin_dir / tool
                script.write_text(
                    f'#!/usr/bin/env bash\necho "{tool} $*" >> "{trap}"\nexit 99\n',
                    encoding="utf-8",
                )
                script.chmod(0o755)
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": str(sandbox),
            }
            for phase in PHASES:
                result = make(root, phase, env=env)
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(list(PHASES), phases_log(root))
            self.assertFalse(trap.exists(), "facade reached a forbidden tool")


if __name__ == "__main__":
    unittest.main()
