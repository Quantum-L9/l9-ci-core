"""Compiler V2 target ownership and documentation contracts.

The root Makefile is a public façade; ``Repo.mk`` is deterministic compiler
output; and ``Repo.local.mk`` adds Core-specific helpers without owning a
standard capability or any Governance transition.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_MK = ROOT / "Repo.mk"
REPO_LOCAL = ROOT / "Repo.local.mk"
PLAN = ROOT / "tools" / "l9_make" / "default-capability-plan.json"
AGENTS = ROOT / "AGENTS.md"
RUNTIME_DOC = ROOT / "docs" / "repository-execution-runtime.md"

TARGET = re.compile(r"(?m)^(?P<name>[a-z][a-z0-9-]*):(?!=)")
RECIPE_SCRIPT = re.compile(r"\$\(PYTHON\)\s+(?P<script>tools/[\w/]+\.py)")
STANDARD_CAPABILITIES = {
    "doctor",
    "setup",
    "build",
    "lint",
    "test",
    "validate",
    "check",
    "package",
    "generate",
    "benchmark",
    "status",
    "clean",
}
LOCAL_RUNTIME_TARGETS = {
    "change-policy",
    "agent-check",
    "reconcile",
    "make-render",
    "make-check",
}
EXPECTED_SCRIPT_TARGETS = {
    "attest-control-plane": "tools/verify_control_plane.py",
    "check-release-writers": "tools/check_release_writers.py",
}
ALL_LOCAL_TARGETS = LOCAL_RUNTIME_TARGETS | set(EXPECTED_SCRIPT_TARGETS)


def declared_targets(path: Path) -> set[str]:
    return set(TARGET.findall(path.read_text(encoding="utf-8")))


def phony_targets(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    declaration = joined.split(".PHONY:")[1].splitlines()[0]
    return set(declaration.split())


class RepoMkTargetTests(unittest.TestCase):
    def test_generated_targets_match_plan_and_are_all_phony(self) -> None:
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        expected = {
            "repo-capabilities",
            *(f"repo-{item['name']}" for item in plan["capabilities"]),
        }
        self.assertEqual(expected, declared_targets(REPO_MK))
        self.assertEqual(expected, phony_targets(REPO_MK))
        self.assertEqual(
            STANDARD_CAPABILITIES,
            {item["name"] for item in plan["capabilities"]},
        )

    def test_generated_adapter_exposes_all_three_capability_states(self) -> None:
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        states = {item["state"] for item in plan["capabilities"]}
        self.assertEqual({"supported", "not_required", "unsupported"}, states)
        text = REPO_MK.read_text(encoding="utf-8")
        self.assertIn("L9_REPO_SUPPORTED_CAPABILITIES", text)
        self.assertIn("L9_REPO_NOT_REQUIRED_CAPABILITIES", text)
        self.assertIn("L9_REPO_UNSUPPORTED_CAPABILITIES", text)
        self.assertIn("NOT_REQUIRED: build", text)
        self.assertIn("UNSUPPORTED: generate", text)

    def test_local_targets_are_only_core_extension_set(self) -> None:
        self.assertEqual(ALL_LOCAL_TARGETS, declared_targets(REPO_LOCAL))
        self.assertEqual(ALL_LOCAL_TARGETS, phony_targets(REPO_LOCAL))
        self.assertNotIn("status", declared_targets(REPO_LOCAL))

    def test_local_compiler_targets_reach_the_renderer(self) -> None:
        text = REPO_LOCAL.read_text(encoding="utf-8")
        for target, command in (("make-render", "render"), ("make-check", "check")):
            with self.subTest(target=target):
                recipe = re.search(rf"(?m)^{target}:.*\n\t(?P<body>.+)$", text)
                self.assertIsNotNone(recipe)
                assert recipe is not None
                self.assertIn("$(L9_MAKE)", recipe.group("body"))
                self.assertIn(f" {command} ", recipe.group("body"))
                self.assertIn("--local Repo.local.mk", recipe.group("body"))
                self.assertIn("--makefile Makefile", recipe.group("body"))

    def test_script_targets_invoke_existing_scripts(self) -> None:
        scripts = set(RECIPE_SCRIPT.findall(REPO_LOCAL.read_text(encoding="utf-8")))
        self.assertEqual(set(EXPECTED_SCRIPT_TARGETS.values()), scripts)
        for script in scripts:
            self.assertTrue((ROOT / script).is_file(), script)

    def test_generated_and_local_layers_own_no_governance_target(self) -> None:
        for path in (REPO_MK, REPO_LOCAL):
            with self.subTest(path=path.name):
                declared = declared_targets(path)
                self.assertFalse({"pr", "push", "release", "deploy"} & declared)
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("git push", text)
                self.assertNotIn("gh pr", text)

    def test_compiler_and_release_targets_are_documented_for_operators(self) -> None:
        runtime = RUNTIME_DOC.read_text(encoding="utf-8")
        for target, script in EXPECTED_SCRIPT_TARGETS.items():
            with self.subTest(target=target):
                self.assertIn(f"`make {target}`", runtime)
                self.assertIn(script, runtime)
        for target in ("make-render", "make-check", "capabilities"):
            self.assertIn(f"`make {target}`", runtime)

    def test_compiler_and_release_targets_are_documented_for_agents(self) -> None:
        agents = AGENTS.read_text(encoding="utf-8")
        for target in (
            *EXPECTED_SCRIPT_TARGETS,
            "make-render",
            "make-check",
            "capabilities",
        ):
            with self.subTest(target=target):
                self.assertIn(f"make {target}", agents)


if __name__ == "__main__":
    unittest.main()
