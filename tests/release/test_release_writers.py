"""Namespace-aware release-writer uniqueness (``tools/check_release_writers.py``).

Three tag namespaces exist and must not be conflated: exact
``vMAJOR.MINOR.PATCH`` Core releases, written only by
``docs/release/tag-and-release.sh``; and the two moving compatibility tags
``v1`` (Core self-references) and ``v2`` (installer and bounded integration
channel), both written only by ``tools/publish_consumer_ci_tag.sh``. The rule
under test is namespace ownership — "only one ``git tag`` may exist in the
repository" is the wrong rule, and these tests fail if the checker ever
degrades into it.

Synthetic repository roots keep each assertion to one behaviour; the real tree
is asserted separately so the contract is proved against the shipped scripts,
not only against fixtures.

:class:`MovingTagPromotionTests` covers the other half of the lifecycle: who
may move a moving tag is namespace ownership, but *what it may be moved onto*
is the promotion gate, and both must hold for the tag to be trustworthy.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools" / "check_release_writers.py"

EXACT_WRITER = "docs/release/tag-and-release.sh"
TRANSITIONAL_WRITER = "tools/publish_consumer_ci_tag.sh"

CONTRACT = """\
schema: l9.release-plane/v1
release_writers:
  namespaces:
    exact_core_release:
      pattern: '^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'
      authorized_writer: docs/release/tag-and-release.sh
    transitional_consumer_installer:
      pattern: '^v2$'
      authorized_writer: tools/publish_consumer_ci_tag.sh
"""

# The shape of the real exact-release writer: an operator-supplied version
# constrained to exact semver, so `v${VERSION}` provably cannot be `v2`.
GUARDED_EXACT_WRITER = """\
#!/usr/bin/env bash
set -euo pipefail
VERSION="${1:-}"
VERSION="${VERSION#v}"
if ! [[ "${VERSION}" =~ ^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$ ]]; then
  echo "not an exact version" >&2
  exit 1
fi
RELEASE_TAG="v${VERSION}"
git tag -a "${RELEASE_TAG}" "${TARGET}" -m "core ${RELEASE_TAG}"
git push origin "${RELEASE_TAG}"
"""

TRANSITIONAL_SOURCE = """\
#!/usr/bin/env bash
set -euo pipefail
git tag v2 "$(git rev-parse HEAD)"
"""


def load_checker():
    spec = importlib.util.spec_from_file_location("check_release_writers", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseWriterFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_checker()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.write(".l9/release-plane.yaml", CONTRACT)
        self.write(EXACT_WRITER, GUARDED_EXACT_WRITER)
        self.write(TRANSITIONAL_WRITER, TRANSITIONAL_SOURCE)

    def write(self, relative: str, body: str) -> pathlib.Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def check(self):
        return self.module.check(self.tmp)

    def writers_for(self, namespace: str) -> list[str]:
        report = self.check()
        return sorted(
            {site.path for site in report.sites if site.namespace == namespace}
        )

    def assert_violation_mentions(self, *fragments: str) -> None:
        report = self.check()
        self.assertFalse(report.ok, "expected a violation")
        joined = "\n".join(report.violations)
        for fragment in fragments:
            self.assertIn(fragment, joined)

    # -- the conforming baseline ------------------------------------------

    def test_two_authorized_writers_conform(self) -> None:
        report = self.check()
        self.assertTrue(report.ok, report.violations)
        self.assertEqual([EXACT_WRITER], self.writers_for("exact_core_release"))
        self.assertEqual(
            [TRANSITIONAL_WRITER],
            self.writers_for("transitional_consumer_installer"),
        )

    def test_exit_code_is_zero_when_conforming(self) -> None:
        self.assertEqual(0, self.module.main(["--root", str(self.tmp)]))

    def test_exit_code_is_two_on_a_violation(self) -> None:
        self.write("scripts/rogue.sh", "git tag -a v9.9.9 HEAD\n")
        self.assertEqual(2, self.module.main(["--root", str(self.tmp)]))

    # -- exact vX.Y.Z namespace -------------------------------------------

    def test_second_exact_release_writer_fails(self) -> None:
        self.write("scripts/rogue.sh", "git tag -a v3.0.0 HEAD -m rogue\n")
        self.assert_violation_mentions("exact_core_release", "scripts/rogue.sh")

    def test_second_exact_release_writer_in_a_workflow_fails(self) -> None:
        self.write(
            ".github/workflows/rogue.yml",
            "jobs:\n  j:\n    steps:\n"
            "      - run: gh release create v3.0.0 --notes rogue\n",
        )
        self.assert_violation_mentions(
            "exact_core_release", ".github/workflows/rogue.yml"
        )

    def test_moving_an_exact_tag_from_an_unauthorized_path_fails(self) -> None:
        self.write("tools/move.sh", 'git push origin "refs/tags/v3.0.0"\n')
        self.assert_violation_mentions("exact_core_release", "tools/move.sh")

    def test_refs_api_tag_creation_from_an_unauthorized_path_fails(self) -> None:
        self.write(
            ".github/workflows/api.yml",
            "jobs:\n  j:\n    steps:\n"
            "      - run: gh api -X POST repos/o/r/git/refs/tags/v3.0.0\n",
        )
        self.assert_violation_mentions(
            "exact_core_release", ".github/workflows/api.yml"
        )

    # -- transitional v2 namespace ----------------------------------------

    def test_second_transitional_writer_fails(self) -> None:
        self.write("scripts/also_v2.sh", "git tag v2 HEAD\n")
        self.assert_violation_mentions(
            "transitional_consumer_installer", "scripts/also_v2.sh"
        )

    # -- the two namespaces may not cross-write ---------------------------

    def test_exact_writer_touching_v2_fails(self) -> None:
        self.write(EXACT_WRITER, GUARDED_EXACT_WRITER + 'git tag v2 "${TARGET}"\n')
        self.assert_violation_mentions(
            "transitional_consumer_installer",
            EXACT_WRITER,
            "must not cross-write",
        )

    def test_transitional_writer_touching_an_exact_tag_fails(self) -> None:
        self.write(
            TRANSITIONAL_WRITER, TRANSITIONAL_SOURCE + "git tag -a v3.0.0 HEAD -m x\n"
        )
        self.assert_violation_mentions(
            "exact_core_release", TRANSITIONAL_WRITER, "must not cross-write"
        )

    # -- fail closed on an undeterminable target --------------------------

    def test_unresolvable_tag_target_fails_closed(self) -> None:
        self.write("scripts/opaque.sh", 'git tag -a "${MYSTERY}" HEAD -m x\n')
        self.assert_violation_mentions(
            "scripts/opaque.sh", "could not be resolved to a tag namespace"
        )

    def test_unresolvable_release_push_fails_closed(self) -> None:
        self.write("scripts/opaque.sh", 'git push origin "${RELEASE_REF}"\n')
        self.assert_violation_mentions(
            "scripts/opaque.sh", "could not be resolved to a tag namespace"
        )

    def test_release_action_without_a_literal_tag_fails_closed(self) -> None:
        self.write(
            ".github/workflows/rogue.yml",
            "jobs:\n  j:\n    steps:\n      - uses: softprops/action-gh-release@abc\n",
        )
        self.assert_violation_mentions(
            ".github/workflows/rogue.yml", "could not be resolved to a tag namespace"
        )

    def test_missing_authorized_writer_fails(self) -> None:
        (self.tmp / EXACT_WRITER).unlink()
        self.assert_violation_mentions("exact_core_release", "does not mutate it")

    # -- prose and read-only inspection are not writers --------------------

    def test_documentation_mentioning_git_tag_is_not_a_writer(self) -> None:
        self.write(
            "docs/release/HOWTO.md",
            "Run `git tag -a v3.0.0` and then `gh release create v3.0.0`.\n",
        )
        self.assertTrue(self.check().ok)

    def test_shell_comments_mentioning_releases_are_not_writers(self) -> None:
        self.write(
            "scripts/notes.sh",
            "#!/usr/bin/env bash\n"
            "# Historically this ran: git tag -a v3.0.0 && git push origin v3.0.0\n"
            "# and gh release create v3.0.0.\n"
            "true\n",
        )
        self.assertTrue(self.check().ok)

    def test_yaml_comments_mentioning_releases_are_not_writers(self) -> None:
        self.write(
            ".github/workflows/notes.yml",
            "# gh release create v3.0.0 used to run here\n"
            "jobs:\n  j:\n    steps:\n      - run: true\n",
        )
        self.assertTrue(self.check().ok)

    def test_python_docstrings_mentioning_releases_are_not_writers(self) -> None:
        self.write(
            "tools/notes.py",
            '"""Explains git tag -a v3.0.0 and gh release create v3.0.0."""\n'
            'MESSAGE = "git tag v2"\n',
        )
        self.assertTrue(self.check().ok)

    def test_printed_instructions_are_not_writers(self) -> None:
        self.write(
            "scripts/hint.sh",
            "#!/usr/bin/env bash\n"
            'echo "to publish: git push origin v2"\n'
            'printf "%s\\n" "git tag -a v3.0.0"\n',
        )
        self.assertTrue(self.check().ok)

    def test_read_only_tag_inspection_is_not_a_writer(self) -> None:
        self.write(
            "scripts/inspect.sh",
            "#!/usr/bin/env bash\n"
            "git rev-parse refs/tags/v3.0.0\n"
            "git show-ref --verify --quiet refs/tags/v2\n"
            "git tag --list 'v*'\n"
            "gh release view v3.0.0\n",
        )
        self.assertTrue(self.check().ok)

    def test_branch_push_is_not_a_release_writer(self) -> None:
        self.write(
            "scripts/deploy.sh",
            '#!/usr/bin/env bash\nBRANCH=feature\ngit push origin "${BRANCH}"\n',
        )
        self.assertTrue(self.check().ok)

    def test_container_image_tag_is_not_a_git_tag(self) -> None:
        self.write(
            ".github/workflows/image.yml",
            "jobs:\n  j:\n    steps:\n"
            '      - run: docker buildx build --tag "ghcr.io/o/r:v3.0.0" .\n',
        )
        self.assertTrue(self.check().ok)

    # -- contract sourcing -------------------------------------------------

    def test_expectations_come_from_the_contract(self) -> None:
        self.write(
            ".l9/release-plane.yaml",
            CONTRACT.replace(
                "authorized_writer: docs/release/tag-and-release.sh",
                "authorized_writer: scripts/other-writer.sh",
            ),
        )
        self.assert_violation_mentions("exact_core_release", "does not mutate it")

    def test_absent_contract_is_an_error_not_a_pass(self) -> None:
        (self.tmp / ".l9/release-plane.yaml").unlink()
        with self.assertRaises(self.module.ReleaseWriterError):
            self.check()
        self.assertEqual(3, self.module.main(["--root", str(self.tmp)]))

    def test_contract_without_release_writers_is_an_error(self) -> None:
        self.write(".l9/release-plane.yaml", "schema: l9.release-plane/v1\n")
        with self.assertRaises(self.module.ReleaseWriterError):
            self.check()


class RealTreeReleaseWriterTests(unittest.TestCase):
    """The shipped tree, not a fixture, must satisfy the invariant."""

    def setUp(self) -> None:
        self.module = load_checker()
        self.namespaces, self.validator = self.module.load_contract(ROOT)

    def test_repository_has_exactly_one_writer_per_namespace(self) -> None:
        report = self.module.check(ROOT)
        self.assertTrue(report.ok, "\n".join(report.violations))
        for key, namespace in self.namespaces.items():
            writers = sorted(
                {site.path for site in report.sites if site.namespace == key}
            )
            self.assertEqual([namespace.authorized_writer], writers, key)

    def test_the_two_shipped_writers_are_the_declared_ones(self) -> None:
        self.assertEqual(
            {EXACT_WRITER, TRANSITIONAL_WRITER},
            {namespace.authorized_writer for namespace in self.namespaces.values()},
        )
        for namespace in self.namespaces.values():
            self.assertTrue((ROOT / namespace.authorized_writer).is_file())

    def test_contract_names_this_validator(self) -> None:
        self.assertEqual("tools/check_release_writers.py", self.validator)
        self.assertTrue((ROOT / self.validator).is_file())

    def test_exact_and_transitional_patterns_are_disjoint(self) -> None:
        exact = self.namespaces["exact_core_release"].pattern
        transitional = self.namespaces["transitional_consumer_installer"].pattern
        self.assertIsNone(exact.fullmatch("v2"))
        self.assertIsNone(transitional.fullmatch("v2.0.0"))
        self.assertIsNotNone(exact.fullmatch("v2.0.0"))
        self.assertIsNotNone(transitional.fullmatch("v2"))

    def test_moving_tag_namespaces_never_collide_with_exact_releases(self) -> None:
        """`v1` is a pointer; `v1.0.0` is an audit identity. Never the same ref."""
        exact = self.namespaces["exact_core_release"].pattern
        moving = self.namespaces["core_self_reference_compatibility"].pattern
        self.assertIsNotNone(moving.fullmatch("v1"))
        self.assertIsNone(moving.fullmatch("v1.0.0"))
        self.assertIsNone(exact.fullmatch("v1"))
        self.assertIsNotNone(exact.fullmatch("v1.0.0"))
        self.assertIsNone(
            self.namespaces["transitional_consumer_installer"].pattern.fullmatch("v1")
        )

    def test_both_moving_tags_share_one_writer(self) -> None:
        """One lifecycle, one writer.

        A second script force-moving a mutable Core pointer would be duplicate
        ownership of the same act, which is what the release-writer invariant
        exists to prevent -- not two separate concerns.
        """
        self.assertEqual(
            TRANSITIONAL_WRITER,
            self.namespaces["core_self_reference_compatibility"].authorized_writer,
        )
        self.assertEqual(
            TRANSITIONAL_WRITER,
            self.namespaces["transitional_consumer_installer"].authorized_writer,
        )


class MovingTagPromotionTests(unittest.TestCase):
    """The authorized writer's promotion gates, exercised against real git.

    These build a throwaway repository so a real `git tag` is harmless, and
    run the shipped script rather than a re-implementation of it.
    """

    WRITER = ROOT / TRANSITIONAL_WRITER
    ACTIONS = (
        "resolve-consumer-metadata",
        "resolve-governance",
        "provision-sdk",
        "invoke-sdk",
        "validate-bundle",
        "route-artifacts",
        "build-artifact-manifest",
    )

    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / "tools").mkdir(parents=True)
        shutil.copy(self.WRITER, self.repo / TRANSITIONAL_WRITER)
        (self.repo / TRANSITIONAL_WRITER).chmod(0o755)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)
        return self.git("rev-parse", "HEAD")

    def add_core_actions(self, *names: str) -> None:
        for name in names:
            action = self.repo / ".github" / "actions" / name
            action.mkdir(parents=True, exist_ok=True)
            (action / "action.yml").write_text(
                f"name: {name}\nruns:\n  using: composite\n", encoding="utf-8"
            )

    def set_main(self, sha: str) -> None:
        """Publish `sha` as the reviewed mainline the writer checks against."""
        self.git("branch", "-f", "--no-track", "origin-main-source", sha)
        self.git("fetch", ".", "refs/heads/origin-main-source:refs/remotes/origin/main")

    def run_writer(self, *args: str):
        return subprocess.run(
            [str(self.repo / TRANSITIONAL_WRITER), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def tag_target(self, tag: str) -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    # -- the reviewed-mainline gate ---------------------------------------

    def test_promotion_to_a_mainline_commit_succeeds(self) -> None:
        self.add_core_actions(*self.ACTIONS)
        main_sha = self.commit("core actions")
        self.set_main(main_sha)

        result = self.run_writer("v1", main_sha)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(main_sha, self.tag_target("v1"))

    def test_promotion_to_an_unmerged_head_is_refused(self) -> None:
        """A pull request must not hand itself the Core revision under review.

        This is the gate that keeps "move the tag to make CI green" from
        being available as a remedy.
        """
        self.add_core_actions(*self.ACTIONS)
        main_sha = self.commit("core actions")
        self.set_main(main_sha)
        (self.repo / "feature.txt").write_text("unmerged\n", encoding="utf-8")
        feature_sha = self.commit("unmerged feature")

        result = self.run_writer("v1", feature_sha)

        self.assertEqual(2, result.returncode)
        self.assertIn("not an ancestor of origin/main", result.stderr)
        self.assertIsNone(
            self.tag_target("v1"), "no tag may be created on a refused promotion"
        )

    def test_refusal_leaves_an_existing_tag_untouched(self) -> None:
        self.add_core_actions(*self.ACTIONS)
        main_sha = self.commit("core actions")
        self.set_main(main_sha)
        self.git("tag", "-f", "v1", main_sha)
        (self.repo / "feature.txt").write_text("unmerged\n", encoding="utf-8")
        feature_sha = self.commit("unmerged feature")

        self.assertEqual(2, self.run_writer("v1", feature_sha).returncode)
        self.assertEqual(main_sha, self.tag_target("v1"))

    # -- the v1 compatibility gate ----------------------------------------

    def test_v1_refuses_a_target_missing_a_core_primitive(self) -> None:
        """The observed v1 bootstrap defect, as a regression test.

        Stale `v1` resolved to a commit carrying six of the seven Core
        actions. The one it lacked, `resolve-consumer-metadata`, is exactly
        what Organization CI reported as unresolvable at setup. A target that
        is on main is still not automatically a compatible target.
        """
        incomplete = [
            name for name in self.ACTIONS if name != "resolve-consumer-metadata"
        ]
        self.add_core_actions(*incomplete)
        main_sha = self.commit("six of seven core actions")
        self.set_main(main_sha)

        result = self.run_writer("v1", main_sha)

        self.assertEqual(2, result.returncode)
        self.assertIn("not a compatible v1 target", result.stderr)
        self.assertIn("resolve-consumer-metadata", result.stderr)
        self.assertIsNone(self.tag_target("v1"))

    def test_v1_requires_every_declared_primitive(self) -> None:
        for omitted in self.ACTIONS:
            with self.subTest(missing=omitted):
                self.setUp()
                self.add_core_actions(*[n for n in self.ACTIONS if n != omitted])
                main_sha = self.commit("incomplete core actions")
                self.set_main(main_sha)
                result = self.run_writer("v1", main_sha)
                self.assertEqual(2, result.returncode)
                self.assertIn(omitted, result.stderr)

    def test_v2_does_not_require_core_self_reference_primitives(self) -> None:
        """The installer channel is not the Core self-reference surface."""
        (self.repo / "readme.txt").write_text("installer\n", encoding="utf-8")
        main_sha = self.commit("no core actions")
        self.set_main(main_sha)

        result = self.run_writer("v2", main_sha)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(main_sha, self.tag_target("v2"))

    # -- namespace containment --------------------------------------------

    def test_writer_refuses_a_ref_outside_its_namespaces(self) -> None:
        self.add_core_actions(*self.ACTIONS)
        main_sha = self.commit("core actions")
        self.set_main(main_sha)
        for outside in ("v3", "v1.0.0", "main", "latest"):
            with self.subTest(ref=outside):
                result = self.run_writer(outside, main_sha)
                self.assertEqual(2, result.returncode)
                self.assertIn("not a moving compatibility tag", result.stderr)
                self.assertIsNone(self.tag_target(outside))

    def test_writer_refuses_when_mainline_is_unknown(self) -> None:
        """Undeterminable mainline is not a pass."""
        self.add_core_actions(*self.ACTIONS)
        sha = self.commit("core actions")

        result = self.run_writer("v1", sha)

        self.assertEqual(2, result.returncode)
        self.assertIn("origin/main", result.stderr)
        self.assertIsNone(self.tag_target("v1"))


if __name__ == "__main__":
    unittest.main()
