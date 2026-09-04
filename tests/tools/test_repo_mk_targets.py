"""``Repo.mk`` targets must resolve to real, documented tools.

``Repo.mk`` holds the repository-specific half of the command facade: targets
that ``tools/l9_repo`` does not own. A target naming a script that does not
exist, or one that no operator documentation mentions, is a facade that lies —
which is why the ``facade-contract`` gate in ``.l9/repo-workflow.json``
requires tests, agent instructions, and operator documentation to move with it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_MK = ROOT / "Repo.mk"
AGENTS = ROOT / "AGENTS.md"
RUNTIME_DOC = ROOT / "docs" / "repository-execution-runtime.md"

TARGET = re.compile(r"(?m)^(?P<name>[a-z][a-z0-9-]*):(?!=)")
RECIPE_SCRIPT = re.compile(r"\$\(PYTHON\)\s+(?P<script>tools/[\w/]+\.py)")

EXPECTED_TARGETS = {
    "attest-control-plane": "tools/verify_control_plane.py",
    "check-release-writers": "tools/check_release_writers.py",
}


class RepoMkTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = REPO_MK.read_text(encoding="utf-8")

    def test_declared_targets_are_the_expected_set(self) -> None:
        declared = set(TARGET.findall(self.text))
        self.assertEqual(set(EXPECTED_TARGETS), declared)

    def test_every_target_is_phony(self) -> None:
        phony = set()
        for line in self.text.splitlines():
            if line.startswith(".PHONY:"):
                phony.update(line.removeprefix(".PHONY:").split())
        self.assertEqual(set(EXPECTED_TARGETS), phony)

    def test_every_target_invokes_an_existing_script(self) -> None:
        scripts = set(RECIPE_SCRIPT.findall(self.text))
        self.assertEqual(set(EXPECTED_TARGETS.values()), scripts)
        for script in scripts:
            self.assertTrue((ROOT / script).is_file(), script)

    def test_recipes_use_the_facade_interpreter(self) -> None:
        """``$(PYTHON)`` is the workspace interpreter the facade defines.

        A hard-coded ``python3`` would silently run a different interpreter
        from the one every other target uses.
        """
        for line in self.text.splitlines():
            if line.startswith("\t"):
                self.assertIn("$(PYTHON)", line, line)

    def test_targets_are_documented_for_operators(self) -> None:
        runtime = RUNTIME_DOC.read_text(encoding="utf-8")
        for target, script in EXPECTED_TARGETS.items():
            with self.subTest(target=target):
                self.assertIn(f"`make {target}`", runtime)
                self.assertIn(script, runtime)

    def test_targets_are_documented_for_agents(self) -> None:
        agents = AGENTS.read_text(encoding="utf-8")
        for target in EXPECTED_TARGETS:
            self.assertIn(f"make {target}", agents)


if __name__ == "__main__":
    unittest.main()
