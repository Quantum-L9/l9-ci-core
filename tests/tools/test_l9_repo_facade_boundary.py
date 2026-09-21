"""The generated facade must hold interface, never publication authority.

``l9-ci-core`` used to own two publication paths: Cursor-Governance's
``l9 pr``, and this repository's own ``make pr`` implemented by
``tools.l9_repo.pr()`` on top of ``git push`` and ``gh pr create``. Two owners
for one responsibility is the duplicate-authority defect, so the runtime's half
was deleted and the facade now delegates.

These tests are the executable form of that boundary. They are the regression
protection for adopting the same template in another repository, where a
reintroduced ``git push`` would be a second publication authority again.

The filename must keep the ``test_l9_repo`` prefix: the ``command-facade`` gate
in ``.l9/repo-workflow.json`` discovers ``tests/tools`` with the pattern
``test_l9_repo*.py``, so a differently named file would never run in the very
gate that changing this facade triggers.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l9_repo.__main__ import COMMANDS  # noqa: E402

MAKEFILE = ROOT / "Makefile"
TEMPLATE = ROOT / "tools" / "l9_repo" / "Makefile.template"
REPO_MK = ROOT / "Repo.mk"
REPO_LOCAL = ROOT / "Repo.local.mk"
SCHEMA = ROOT / ".l9" / "repo-workflow.schema.json"
CONFIG = ROOT / ".l9" / "repo-workflow.json"

GIT_PUBLICATION = re.compile(r"git\s+push|gh\s+pr\b")


class FacadeHoldsNoPublicationAuthority(unittest.TestCase):
    def test_facade_invokes_no_git_or_github_publication(self) -> None:
        for path in (MAKEFILE, TEMPLATE, REPO_MK, REPO_LOCAL):
            with self.subTest(path=path.name):
                self.assertIsNone(
                    GIT_PUBLICATION.search(path.read_text(encoding="utf-8")),
                    f"{path.name} must not invoke git push or gh pr",
                )

    def test_generated_facade_binds_no_repository_runtime(self) -> None:
        """The template is portable: no directive may name Core's module.

        Only executable directives are in scope. The header comment documents
        ``python3 -m tools.l9_repo reconcile`` as the recovery path for an
        unparseable Makefile, which is exactly the situation in which ``make``
        cannot help — that instruction is the point, not a violation.

        ``Repo.mk`` is deliberately exempt in full — it is Core's
        implementation adapter and legitimately binds the runtime through
        ``L9_REPO``.
        """
        for path in (MAKEFILE, TEMPLATE):
            with self.subTest(path=path.name):
                directives = [
                    line
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ]
                self.assertNotIn("tools.l9_repo", "\n".join(directives))

    def test_implementation_boundaries_fail_closed(self) -> None:
        """Both layers use ``include`` so a missing boundary cannot silently degrade."""
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^include Repo\.mk$")
        self.assertRegex(text, r"(?m)^include Repo\.local\.mk$")
        self.assertNotRegex(text, r"(?m)^-include Repo\.mk$")
        self.assertNotRegex(text, r"(?m)^-include Repo\.local\.mk$")

    def test_runtime_exposes_no_publication_command(self) -> None:
        self.assertNotIn("push", COMMANDS)
        self.assertNotIn("pr", COMMANDS)

    def test_push_preflight_module_is_gone(self) -> None:
        self.assertFalse((ROOT / "tools" / "l9_repo" / "push_preflight.py").exists())

    def test_publication_config_survives_only_as_deprecated_shape(self) -> None:
        """The contract shape is co-versioned with the pinned Core runtime.

        ``push`` and ``pull_request`` drive no behaviour any more, but they
        stay declared because ``org-ci.yml`` pins
        ``run-repository-verification@<sha>`` to a Core checkout whose
        validator still requires both keys. Dropping them here fails
        organization CI against the current pin. Removing them, and replacing
        ``pull_request.base`` with ``repository.default_branch``, is a
        follow-up once a tolerant Core runtime is pinned.

        This test exists so that removal is a deliberate act with a failing
        assertion attached, rather than something that silently regresses.
        """
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for block in ("push", "pull_request"):
            with self.subTest(block=block):
                self.assertIn(block, config)
                self.assertIn(block, schema["properties"])

    def test_runtime_reads_no_publication_policy_beyond_the_comparison_ref(
        self,
    ) -> None:
        """Only ``pull_request.base`` may still be read, and only as a ref.

        Every other publication key is declared-but-dead. If the runtime starts
        reading one again, publication logic has crept back in.
        """
        source = (ROOT / "tools" / "l9_repo" / "__main__.py").read_text(
            encoding="utf-8"
        )
        # Both quote styles: plain subscripts and f-string subscripts.
        reads = set(
            re.findall(r"""\[['"](push|pull_request)['"]\]\[['"](\w+)['"]\]""", source)
        )
        self.assertEqual(reads, {("pull_request", "base")}, sorted(reads))


class FacadeRoutesToTheRightOwner(unittest.TestCase):
    """``make -n`` proves routing without executing anything."""

    def dry_run(self, target: str) -> str:
        result = subprocess.run(
            ["make", "-n", target],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_repository_verbs_reach_the_repo_leaves(self) -> None:
        for target in ("setup", "validate", "check", "test", "clean", "doctor"):
            with self.subTest(target=target):
                output = self.dry_run(target)
                self.assertIn("tools.l9_repo", output)
                self.assertIsNone(GIT_PUBLICATION.search(output))

    def test_governance_verbs_reach_the_dispatcher(self) -> None:
        for target, verb in (
            ("start", "start"),
            ("workspace-clean", "workspace-clean"),
            ("wiring-check", "wiring-check"),
        ):
            with self.subTest(target=target):
                output = self.dry_run(target)
                self.assertIn(f"l9 {verb}", output)
                self.assertNotIn("tools.l9_repo", output)

    def test_publication_verb_reaches_the_dispatcher_and_nothing_else(self) -> None:
        """``make pr`` must expand to exactly the dispatcher call.

        Asserted against the recipe text rather than ``make -n``: an agent
        session running under the L4 local-autonomy gate is refused any
        command naming a publication target, dry run included.
        """
        recipe = re.search(
            r"(?m)^pr:.*\n\t(?P<body>.+)$", TEMPLATE.read_text(encoding="utf-8")
        )
        self.assertIsNotNone(recipe)
        assert recipe is not None
        body = recipe.group("body").strip()
        self.assertEqual(body, "@$(L9) pr")


if __name__ == "__main__":
    unittest.main()
