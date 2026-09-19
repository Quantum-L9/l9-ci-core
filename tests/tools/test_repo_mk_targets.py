"""``Repo.mk`` targets must resolve to real, documented implementations.

``Repo.mk`` holds the repository-owned half of the command facade. The
generated root ``Makefile`` owns the portable operator vocabulary and
implements nothing: repository verbs delegate to the ``repo-*`` leaves below,
and cross-repository governance verbs delegate to the ``l9`` dispatcher.

A target naming a script that does not exist, or one that no operator
documentation mentions, is a facade that lies — which is why the
``facade-contract`` gate in ``.l9/repo-workflow.json`` requires tests, agent
instructions, and operator documentation to move with it.
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

# Leaves the generated facade delegates its repository verbs to. Each one is
# implemented by the repository execution runtime, never by a standalone
# script, so they are excluded from the script-and-documentation contract.
FACADE_LEAVES = {
    "repo-setup": "setup",
    "repo-validate": "validate",
    "repo-check": "check",
    "repo-test": "test",
    "repo-clean": "clean",
    "repo-doctor": "doctor",
}

# Core-only operator targets. They are not part of the portable vocabulary, so
# they live here rather than in the generated facade.
CORE_RUNTIME_TARGETS = {
    "change-policy": "change-policy",
    "agent-check": "agent-check",
    "status": "status",
    "reconcile": "reconcile",
}

# Release-assurance helpers implemented by standalone scripts.
EXPECTED_TARGETS = {
    "attest-control-plane": "tools/verify_control_plane.py",
    "check-release-writers": "tools/check_release_writers.py",
}

ALL_TARGETS = set(FACADE_LEAVES) | set(CORE_RUNTIME_TARGETS) | set(EXPECTED_TARGETS)


class RepoMkTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = REPO_MK.read_text(encoding="utf-8")

    def test_declared_targets_are_the_expected_set(self) -> None:
        declared = set(TARGET.findall(self.text))
        self.assertEqual(ALL_TARGETS, declared)

    def test_every_target_is_phony(self) -> None:
        # ``.PHONY`` is written as one backslash-continued list, so join the
        # continuations before splitting it into names.
        joined = re.sub(r"\\\s*\n\s*", " ", self.text)
        declaration = joined.split(".PHONY:")[1].splitlines()[0]
        self.assertEqual(ALL_TARGETS, set(declaration.split()))

    def test_script_targets_invoke_an_existing_script(self) -> None:
        scripts = set(RECIPE_SCRIPT.findall(self.text))
        self.assertEqual(set(EXPECTED_TARGETS.values()), scripts)
        for script in scripts:
            self.assertTrue((ROOT / script).is_file(), script)

    def test_recipes_use_the_facade_interpreter(self) -> None:
        """Every recipe runs through ``$(PYTHON)``.

        ``L9_REPO`` expands to ``$(PYTHON) -m tools.l9_repo``, so both recipe
        forms reach the same workspace interpreter. A hard-coded ``python3``
        would silently run a different one from every other target.
        """
        # The ``.PHONY`` list is tab-continued, so its lines are tab-indented
        # without being recipes. Drop that declaration before scanning.
        body = re.sub(r"(?s)\.PHONY:.*?(?<!\\)\n(?=\S|\n)", "", self.text)
        recipes = [line for line in body.splitlines() if line.startswith("\t")]
        self.assertTrue(recipes, "no recipe lines found")
        for line in recipes:
            self.assertTrue(
                "$(PYTHON)" in line or "$(L9_REPO)" in line,
                line,
            )

    def test_repo_mk_owns_no_publication_target(self) -> None:
        """Publication is Cursor-Governance's, reached as ``make pr`` -> ``l9 pr``.

        ``Repo.mk`` is repository-owned implementation. A ``pr`` or ``push``
        target here would reintroduce the second publication authority this
        split exists to delete.
        """
        declared = set(TARGET.findall(self.text))
        self.assertNotIn("pr", declared)
        self.assertNotIn("push", declared)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("gh pr", self.text)

    def test_facade_leaves_reach_the_runtime_command(self) -> None:
        for target, command in FACADE_LEAVES.items():
            with self.subTest(target=target):
                recipe = re.search(
                    rf"(?m)^{re.escape(target)}:.*\n\t(?P<body>.+)$", self.text
                )
                self.assertIsNotNone(recipe, target)
                assert recipe is not None
                self.assertIn("$(L9_REPO)", recipe.group("body"))
                self.assertTrue(
                    recipe.group("body").rstrip().endswith(command),
                    recipe.group("body"),
                )

    def test_script_targets_are_documented_for_operators(self) -> None:
        runtime = RUNTIME_DOC.read_text(encoding="utf-8")
        for target, script in EXPECTED_TARGETS.items():
            with self.subTest(target=target):
                self.assertIn(f"`make {target}`", runtime)
                self.assertIn(script, runtime)

    def test_script_targets_are_documented_for_agents(self) -> None:
        agents = AGENTS.read_text(encoding="utf-8")
        for target in EXPECTED_TARGETS:
            self.assertIn(f"make {target}", agents)


if __name__ == "__main__":
    unittest.main()
