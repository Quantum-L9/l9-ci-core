"""End-to-end releases of throwaway repositories, one per ecosystem.

Git runs for real against a bare ``origin``; GitHub's Releases API is the
in-memory ``FakeGitHub`` at the engine's transport boundary; ``uv`` is the
non-reproducible-envelope shim from ``support`` and ``npm`` is the real tool.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from support import (  # noqa: E402
    REPOSITORY,
    FakeGitHub,
    Fixture,
    git,
    node_project,
    python_project,
    temporary,
)

from l9_release.__main__ import run  # noqa: E402
from l9_release.errors import ReleaseInfrastructureError, ReleaseRefused  # noqa: E402
from l9_release.gitops import create_exact_tag, write_major_tag  # noqa: E402


class ReleaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = temporary()
        self.base = pathlib.Path(self._tmp.name)
        self.fixture = Fixture(self.base)
        self.github = FakeGitHub()
        patcher = mock.patch.dict("os.environ", self.fixture.env())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.rounds = 0

    def engine(self, *argv: str) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = run(
                [
                    "--workspace",
                    str(self.fixture.work),
                    "--repository",
                    REPOSITORY,
                    *argv,
                ],
                transport=self.github,
            )
        self.assertEqual(0, code)
        return output.getvalue()

    def plan(self) -> dict[str, str]:
        text = self.engine("plan")
        return json.loads(text[text.index("{") :])

    def build(self) -> pathlib.Path:
        self.rounds += 1
        dist = self.base / f"dist-{self.rounds}"
        self.engine(
            "build",
            "--out",
            str(dist),
            "--scratch",
            str(self.base / f"scratch-{self.rounds}"),
            "--phases",
            "verified-upstream",
        )
        return dist

    def release(self) -> pathlib.Path:
        dist = self.build()
        self.engine("publish", "--dist", str(dist))
        return dist


class PythonUvReleaseTests(ReleaseCase):
    def test_full_release_then_idempotent_rerun(self) -> None:
        python_project(self.fixture, "1.0.0")
        head = self.fixture.commit("release 1.0.0")
        planned = self.plan()
        self.assertEqual(
            ("true", "true", "true", "true", "v1.0.0"),
            (
                planned["create_tag"],
                planned["create_release"],
                planned["upload_assets"],
                planned["work_pending"],
                planned["exact_tag"],
            ),
        )

        dist = self.release()
        sums = (dist / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertIn("release_fixture-1.0.0.tar.gz", sums)
        self.assertIn("release_fixture-1.0.0-py3-none-any.whl", sums)
        self.assertEqual(head, self.fixture.remote_tag("v1.0.0"))
        kind = git(
            self.fixture.work,
            "cat-file",
            "-t",
            git(self.fixture.work, "ls-remote", "origin", "refs/tags/v1.0.0").split()[
                0
            ],
        ).strip()
        self.assertEqual("tag", kind)  # annotated
        release = self.github.releases["v1.0.0"]
        self.assertIn("- Release 1.0.0.", release["body"])
        self.assertIn(f"- Commit: `{head}`", release["body"])
        self.assertIn(sums, release["body"])
        self.assertEqual(
            sorted(
                [*(line.split("  ")[1] for line in sums.splitlines()), "SHA256SUMS"]
            ),
            sorted(asset["name"] for asset in release["assets"]),
        )
        self.assertIsNone(self.fixture.remote_tag("v1"))  # not declared

        rerun = self.plan()
        self.assertEqual(
            ("true", "false"), (rerun["is_release_commit"], rerun["work_pending"])
        )
        uploads = len(self.github.blobs)
        self.engine("publish", "--dist", str(dist))
        self.assertEqual(uploads, len(self.github.blobs))
        self.assertIn("asset(s) verified", self.engine("verify", "--dist", str(dist)))

    def test_builds_are_reproducible_across_independent_runs(self) -> None:
        python_project(self.fixture, "1.0.0")
        self.fixture.commit("release 1.0.0")
        first, second = self.build(), self.build()
        self.assertEqual(
            (first / "SHA256SUMS").read_bytes(), (second / "SHA256SUMS").read_bytes()
        )

    def test_partial_publication_converges_on_rerun(self) -> None:
        python_project(self.fixture, "1.0.0")
        self.fixture.commit("release 1.0.0")
        dist = self.build()
        self.github.fail_upload_after = 1
        with self.assertRaisesRegex(ReleaseInfrastructureError, "HTTP 502"):
            run(
                [
                    "--workspace",
                    str(self.fixture.work),
                    "--repository",
                    REPOSITORY,
                    "publish",
                    "--dist",
                    str(dist),
                ],
                transport=self.github,
            )
        stranded = self.plan()
        self.assertEqual(
            ("false", "false", "true"),
            (
                stranded["create_tag"],
                stranded["create_release"],
                stranded["upload_assets"],
            ),
        )
        self.github.fail_upload_after = None
        self.engine("publish", "--dist", str(dist))
        self.assertEqual("false", self.plan()["work_pending"])

    def test_next_release_and_non_bump_push(self) -> None:
        python_project(self.fixture, "1.0.0")
        self.fixture.commit("release 1.0.0")
        self.release()
        self.fixture.write("README.md", "docs only\n")
        self.fixture.commit("docs")
        self.assertEqual(
            ("false", "false"),
            (self.plan()["is_release_commit"], self.plan()["work_pending"]),
        )
        python_project(self.fixture, "1.1.0")
        head = self.fixture.commit("release 1.1.0")
        self.release()
        self.assertEqual(head, self.fixture.remote_tag("v1.1.0"))


class RefusalTests(ReleaseCase):
    def refused(self, *argv: str) -> str:
        with (
            self.assertRaises(ReleaseRefused) as caught,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run(
                [
                    "--workspace",
                    str(self.fixture.work),
                    "--repository",
                    REPOSITORY,
                    *argv,
                ],
                transport=self.github,
            )
        return str(caught.exception)

    def test_dirty_tree(self) -> None:
        python_project(self.fixture, "1.0.0")
        self.fixture.commit("release 1.0.0")
        self.fixture.write("src/release_fixture/__init__.py", "VERSION = 'dirty'\n")
        message = self.refused(
            "build",
            "--out",
            str(self.base / "d"),
            "--scratch",
            str(self.base / "s"),
            "--phases",
            "verified-upstream",
        )
        self.assertIn("dirty", message)

    def test_version_not_ahead(self) -> None:
        python_project(self.fixture, "2.0.0")
        self.fixture.commit("release 2.0.0")
        self.release()
        python_project(self.fixture, "1.5.0")
        self.fixture.commit("regress")
        self.assertIn("not ahead", self.refused("plan"))

    def test_no_changelog_section(self) -> None:
        python_project(self.fixture, "1.0.0", dated=False)
        self.fixture.commit("release 1.0.0")
        self.assertIn("CHANGELOG", self.refused("plan"))

    def test_existing_exact_tag_is_never_overwritten(self) -> None:
        python_project(self.fixture, "1.0.0")
        first = self.fixture.commit("release 1.0.0")
        create_exact_tag(self.fixture.work, "origin", "v1.0.0", first, "v1.0.0")
        self.fixture.write("README.md", "later\n")
        second = self.fixture.commit("later")
        git(self.fixture.work, "tag", "--delete", "v1.0.0")
        with self.assertRaisesRegex(ReleaseInfrastructureError, "git push"):
            create_exact_tag(self.fixture.work, "origin", "v1.0.0", second, "again")
        self.assertEqual(first, self.fixture.remote_tag("v1.0.0"))
        # The plan sees the tag elsewhere: this push is not a release.
        self.assertEqual("false", self.plan()["is_release_commit"])

    def test_tag_writers_refuse_the_wrong_namespace(self) -> None:
        python_project(self.fixture, "1.0.0")
        head = self.fixture.commit("release 1.0.0")
        with self.assertRaisesRegex(ReleaseInfrastructureError, "non-exact"):
            create_exact_tag(self.fixture.work, "origin", "v1", head, "m")
        with self.assertRaisesRegex(ReleaseInfrastructureError, "non-major"):
            write_major_tag(self.fixture.work, "origin", "v1.0.0", head, None)

    def test_core_repository_refused(self) -> None:
        python_project(self.fixture, "1.0.0")
        self.fixture.commit("release 1.0.0")
        with self.assertRaisesRegex(ReleaseRefused, "tag-and-release.sh"):
            run(
                [
                    "--workspace",
                    str(self.fixture.work),
                    "--repository",
                    "Quantum-L9/l9-ci-core",
                    "plan",
                ],
                transport=self.github,
            )

    def test_repository_without_declaration_is_untouched(self) -> None:
        self.fixture.write("README.md", "no release declaration\n")
        self.fixture.commit("init")
        output = self.engine("plan")
        self.assertIn("does not release", output)
        self.assertEqual([], self.github.calls)


class NodeNpmReleaseTests(ReleaseCase):
    def test_release_with_forward_only_major_tag(self) -> None:
        node_project(self.fixture, "4.0.0")
        first = self.fixture.commit("release 4.0.0")
        self.assertEqual("create", self.plan()["major_verdict"])
        dist = self.release()
        self.assertEqual(
            ["release-fixture-4.0.0.tgz"],
            [
                line.split("  ")[1]
                for line in (dist / "SHA256SUMS").read_text().splitlines()
            ],
        )
        self.assertEqual(first, self.fixture.remote_tag("v4"))
        self.assertEqual(first, self.fixture.remote_tag("v4.0.0"))

        node_project(self.fixture, "4.1.0")
        second = self.fixture.commit("release 4.1.0")
        self.assertEqual(
            ("advance", first),
            (self.plan()["major_verdict"], self.plan()["previous_major_sha"]),
        )
        self.release()
        self.assertEqual(second, self.fixture.remote_tag("v4"))
        self.assertEqual(first, self.fixture.remote_tag("v4.0.0"))  # exact untouched
        self.assertEqual("false", self.plan()["work_pending"])

    def test_major_tag_lease_refuses_a_racing_writer(self) -> None:
        node_project(self.fixture, "4.0.0")
        first = self.fixture.commit("release 4.0.0")
        self.release()
        node_project(self.fixture, "4.1.0")
        second = self.fixture.commit("release 4.1.0")
        with self.assertRaisesRegex(ReleaseInfrastructureError, "git push"):
            write_major_tag(self.fixture.work, "origin", "v4", second, "0" * 40)
        self.assertEqual(first, self.fixture.remote_tag("v4"))


if __name__ == "__main__":
    unittest.main()
