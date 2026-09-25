"""Negative architecture: no publication, no Governance, one Make authority.

The primary proof is structural (the facade's tiny target surface and the
runtime's import graph). The token scans are regression tripwires.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from v2_fixtures import ROOT  # noqa: E402

from l9_repo.__main__ import COMMANDS  # noqa: E402
from l9_repo.contract import FACADE_TEMPLATES, make_target_declarations  # noqa: E402

MAKEFILE = ROOT / "Makefile"
REPO_MK = ROOT / "Repo.mk"
RUNTIME = ROOT / "tools" / "l9_repo"
BRIDGE = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"

GOVERNANCE_TARGETS = frozenset(
    {
        "pr",
        "push",
        "publish",
        "release",
        "deploy",
        "start",
        "workspace-clean",
        "wiring-check",
    }
)
FACADE_TOKENS = (
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
INVOCATION_TOKENS = ("git push", "gh pr", "l9 pr", "$(L9)", "L9 ?=", "@l9 ", "\tl9 ")
STDLIB_OR_PACKAGE = re.compile(r"^(l9_repo|\.)")


class GeneratedFacadeBoundaryTests(unittest.TestCase):
    def facades(self) -> dict[str, str]:
        texts = {
            name: path.read_text(encoding="utf-8")
            for name, path in FACADE_TEMPLATES.items()
        }
        texts["Makefile"] = MAKEFILE.read_text(encoding="utf-8")
        return texts

    def test_facades_declare_no_governance_target(self) -> None:
        for name, text in self.facades().items():
            with self.subTest(facade=name):
                declared = {target for _, target in make_target_declarations(text)}
                self.assertEqual(set(), declared & GOVERNANCE_TARGETS)

    def test_facades_contain_no_publication_tokens(self) -> None:
        for name, text in self.facades().items():
            for token in FACADE_TOKENS:
                with self.subTest(facade=name, token=token):
                    self.assertNotIn(token, text)

    def test_no_dispatcher_variable_survives_anywhere_in_make_layers(self) -> None:
        for path in (MAKEFILE, REPO_MK, *FACADE_TEMPLATES.values()):
            text = path.read_text(encoding="utf-8")
            for token in INVOCATION_TOKENS:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)


class RepositoryLayerBoundaryTests(unittest.TestCase):
    def test_core_repo_mk_declares_no_governance_target(self) -> None:
        declared = {
            target
            for _, target in make_target_declarations(
                REPO_MK.read_text(encoding="utf-8")
            )
        }
        self.assertEqual(set(), declared & GOVERNANCE_TARGETS)

    def test_runtime_exposes_no_publication_command(self) -> None:
        self.assertEqual(set(), set(COMMANDS) & GOVERNANCE_TARGETS)
        self.assertNotIn("migrate-v1", COMMANDS)
        self.assertNotIn("init", COMMANDS)

    def test_runtime_imports_only_stdlib_and_itself(self) -> None:
        for path in sorted(RUNTIME.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = ["." * node.level + (node.module or "")]
                for name in names:
                    top = name.split(".")[0]
                    with self.subTest(module=path.name, imported=name):
                        self.assertTrue(
                            name.startswith(".") or top in sys.stdlib_module_names,
                            f"{path.name} imports {name}; only stdlib and package-relative "
                            "imports are allowed",
                        )

    def test_runtime_invokes_no_publication_or_governance_tool(self) -> None:
        for path in sorted(RUNTIME.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in ("git push", "gh pr", "l9 pr", "$(L9)"):
                with self.subTest(path=path.relative_to(ROOT).as_posix(), token=token):
                    self.assertNotIn(token, text)
            self.assertNotIn("RESERVED_GOVERNANCE_TARGETS", text)

    def test_no_shell_execution_primitives_in_runtime(self) -> None:
        for path in sorted(RUNTIME.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("shell=True", text)
                self.assertNotIn("os.system", text)
                self.assertNotIn("shlex", text)
                self.assertNotIn("eval(", text)

    def test_one_make_authority_no_superseded_compiler_reference(self) -> None:
        """Prose may name the superseded model to forbid it; code may not."""

        self.assertFalse((ROOT / "tools" / "l9_make").exists())
        self.assertFalse((ROOT / "Repo.local.mk").exists())
        self.assertFalse((ROOT / "tools" / "l9_repo" / "Makefile.template").exists())
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(
                (
                    ".git/",
                    ".venv/",
                    ".l9/runtime/",
                    ".l9/pr/",
                    ".l9/autonomy/",
                    ".l9/memory/",
                    "artifacts/",
                )
            ):
                continue
            if (
                rel.startswith("tests/repo_execution/")
                or rel.endswith((".md", ".sha256"))
                or "__pycache__" in rel
            ):
                continue
            if rel.startswith((".mypy_cache/", ".ruff_cache/", ".pytest_cache/")):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in (
                "l9.make-plan",
                "tools.l9_make",
                "tools/l9_make",
                "default-capability-plan",
                "Repo.local.mk",
                "make-render",
                "repo-capabilities",
                "L9_REPO_CAPABILITIES",
            ):
                if marker in text:
                    offenders.append(f"{rel}: {marker}")
        self.assertEqual([], offenders)

    def test_admission_bridge_is_untouched_by_adoption(self) -> None:
        text = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("from l9_repo.__main__ import RepositoryWorkflow", text)
        self.assertIn('_write_output("status", "fail")', text)
        self.assertNotIn("contract-mode", text)
        self.assertNotIn("migration", text)


if __name__ == "__main__":
    unittest.main()
