"""Namespace-aware release-writer uniqueness (``tools/check_release_writers.py``).

Two tag namespaces exist and must not be conflated: exact ``vMAJOR.MINOR.PATCH``
Core releases, written only by ``docs/release/tag-and-release.sh``, and the
transitional ``v2`` installer tag, written only by
``tools/publish_consumer_ci_tag.sh``. The rule under test is namespace
ownership — "only one ``git tag`` may exist in the repository" is the wrong
rule, and these tests fail if the checker ever degrades into it.

Synthetic repository roots keep each assertion to one behaviour; the real tree
is asserted separately so the contract is proved against the shipped scripts,
not only against fixtures.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
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


if __name__ == "__main__":
    unittest.main()
