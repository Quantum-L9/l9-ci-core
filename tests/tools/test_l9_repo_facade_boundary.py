"""Executable boundary tests for the V2 generated facade."""

from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
TEMPLATE = ROOT / "tools/l9_repo/Makefile.template"
REPO_MK = ROOT / "Repo.mk"
CONTRACT = ROOT / ".l9/repo-workflow.json"


class FacadeBoundaryTests(unittest.TestCase):
    def test_generated_facade_contains_only_portable_abi_routing(self) -> None:
        text = TEMPLATE.read_text(encoding="utf-8")
        for phase in ("setup", "validate", "check", "test"):
            self.assertRegex(text, rf"(?m)^{phase}: repo-{phase}")
        self.assertRegex(text, r"(?m)^include Repo\.mk$")
        self.assertNotIn("pip install", text)
        self.assertNotIn("ruff", text)
        self.assertNotIn("mypy", text)
        self.assertNotIn("pytest", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("gh pr", text)

    def test_core_self_host_contract_contains_no_command_matrix_or_policy(self) -> None:
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        forbidden = {
            "commands",
            "authority",
            "push",
            "pull_request",
            "change_policy",
            "agent_contracts",
            "reporting",
            "status",
        }
        self.assertEqual(set(), forbidden & set(data))
        self.assertEqual({"schema", "facade", "required_phases"}, set(data))

    def test_makefile_is_exact_generated_artifact(self) -> None:
        self.assertEqual(MAKEFILE.read_bytes(), TEMPLATE.read_bytes())

    def test_core_repo_mk_is_the_only_local_implementation(self) -> None:
        text = REPO_MK.read_text(encoding="utf-8")
        for phase in ("setup", "validate", "check", "test"):
            self.assertRegex(text, rf"(?m)^repo-{phase}:")
        self.assertNotIn("git push", text)
        self.assertNotIn("gh pr", text)

    def test_make_routes_required_abi_to_repo_mk(self) -> None:
        for phase in ("setup", "validate", "check", "test"):
            result = subprocess.run(
                ["make", "-n", phase],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(f"repo-{phase}", TEMPLATE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
