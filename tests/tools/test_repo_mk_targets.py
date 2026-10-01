"""Core self-hosting implements the portable V2 ABI only through Repo.mk."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_MK = ROOT / "Repo.mk"
TEMPLATE = ROOT / "tools/l9_repo/Makefile.template"

ABI = {"setup", "validate", "check", "test"}
CORE_TARGETS = {
    "lint",
    "change-policy",
    "agent-check",
    "status",
    "reconcile",
    "verify-generated",
    "attest-control-plane",
    "check-release-writers",
}
EXPECTED = (
    {f"repo-{phase}" for phase in ABI} | {"repo-clean", "repo-doctor"} | CORE_TARGETS
)
TARGET = re.compile(r"(?m)^(?P<name>[a-z][a-z0-9-]*):(?!=)")


class RepoMkTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = REPO_MK.read_text(encoding="utf-8")

    def test_declares_the_v2_implementation_leaves_and_core_helpers(self) -> None:
        self.assertEqual(EXPECTED, set(TARGET.findall(self.text)))

    def test_each_v2_phase_is_explicitly_implemented(self) -> None:
        for phase in ABI:
            recipe = re.search(
                rf"(?ms)^repo-{phase}:\n(?P<body>(?:\t.*\n)+)", self.text
            )
            self.assertIsNotNone(recipe, phase)
            assert recipe is not None
            self.assertNotEqual("", recipe.group("body").strip())

    def test_core_validation_runs_generic_v2_and_core_local_policy_checks(self) -> None:
        recipe = re.search(r"(?ms)^repo-validate:\n(?P<body>(?:\t.*\n)+)", self.text)
        self.assertIsNotNone(recipe)
        assert recipe is not None
        self.assertIn("validate", recipe.group("body"))
        self.assertIn("core-validate", recipe.group("body"))

    def test_generated_facade_routes_only_the_abi(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        for phase in ABI:
            self.assertRegex(template, rf"(?m)^{phase}: repo-{phase}")
        self.assertNotIn(
            "tools/l9_repo",
            "\n".join(
                line
                for line in template.splitlines()
                if not line.lstrip().startswith("#")
            ),
        )

    def test_repo_mk_owns_no_publication_target(self) -> None:
        self.assertNotIn("git push", self.text)
        self.assertNotIn("gh pr", self.text)
        self.assertNotIn("\npr:", self.text)
        self.assertNotIn("\npush:", self.text)


if __name__ == "__main__":
    unittest.main()
