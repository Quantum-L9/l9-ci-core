"""Release runner: registry candidates, dispatch path, schedule enumeration.

The GitHub REST API is stubbed at the engine's transport boundary; the
registry text is a fixture parsed by Core's real registry reader.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.parse
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from support import API  # noqa: E402

from l9_release.errors import ReleaseInfrastructureError, ReleaseRefused  # noqa: E402
from l9_release.github import Response  # noqa: E402
from l9_release.runner import (  # noqa: E402
    OrgClient,
    candidates,
    current_members,
    discover,
    registry_module,
    run,
)

OWNER = "Quantum-L9"


def entry(name: str, lifecycle: str = "current", owner: str = OWNER) -> str:
    return (
        f"  - id: {name.lower()}\n"
        "    coordinate:\n"
        "      provider: github\n"
        f"      organization: {owner}\n"
        f"      repository: {name}\n"
        f"    lifecycle: {lifecycle}\n"
        "    class_ref: l9.repository-class/l9@1\n"
    )


REGISTRY = (
    "schema: l9.repository-registry/v1\n"
    "artifact_id: l9.repository-registry/global@1\n"
    "canonical: true\n"
    "repositories:\n"
    + entry("l9-meta-injector")
    + entry("l9-ci-core")
    + entry("l9-constellation-topology")
    + entry("l9-old-thing", lifecycle="retired")
    + entry("archived-one")
    + entry("undeclared")
    + entry("flaky")
    + entry("elsewhere", owner="Other-Org")
)


class FakeOrgApi:
    """``GET /repos/{r}`` and ``GET /repos/{r}/contents/.l9/release.json``."""

    def __init__(self) -> None:
        self.repos = {
            "l9-meta-injector": {"default_branch": "main"},
            "l9-constellation-topology": {"default_branch": "main"},
            "archived-one": {"default_branch": "main", "archived": True},
            "undeclared": {"default_branch": "trunk"},
            "flaky": {"default_branch": "main"},
        }
        self.declared = {
            "l9-meta-injector",
            "l9-constellation-topology",
            "archived-one",
        }
        self.calls: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        parts = urllib.parse.urlsplit(url)
        self.calls.append(f"{method} {parts.path}?{parts.query}")
        assert method == "GET", method
        segments = parts.path.split("/")
        name = segments[3]
        if name == "flaky":
            return Response(500, b"{}")
        if name not in self.repos:
            return Response(404, b"{}")
        if len(segments) == 4:
            return Response(
                200,
                json.dumps(
                    {"full_name": f"{OWNER}/{name}", **self.repos[name]}
                ).encode(),
            )
        ref = urllib.parse.parse_qs(parts.query)["ref"][0]
        assert ref == self.repos[name]["default_branch"], (name, ref)
        assert parts.path.endswith("/contents/.l9/release.json"), parts.path
        return Response(200 if name in self.declared else 404, b"{}")


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reader = registry_module()
        self.members = current_members(REGISTRY, OWNER, self.reader)

    def test_current_members_of_the_owner_without_core(self) -> None:
        self.assertEqual(
            [
                "archived-one",
                "flaky",
                "l9-constellation-topology",
                "l9-meta-injector",
                "undeclared",
            ],
            self.members,
        )

    def test_schedule_takes_every_member(self) -> None:
        self.assertEqual(self.members, candidates(self.members, OWNER, None))

    def test_dispatch_narrows_to_one_member(self) -> None:
        for given in (
            "l9-meta-injector",
            "Quantum-L9/l9-meta-injector",
            "quantum-l9/L9-META-INJECTOR",
        ):
            with self.subTest(given=given):
                self.assertEqual(
                    ["l9-meta-injector"], candidates(self.members, OWNER, given)
                )

    def test_dispatch_refusals(self) -> None:
        for given, reason in (
            ("Other-Org/l9-meta-injector", "not a repository of"),
            ("l9-ci-core", "tag-and-release.sh"),
            ("l9-old-thing", "not a current member"),
            ("not-registered", "not a current member"),
            ("bad name", "invalid repository name"),
        ):
            with (
                self.subTest(given=given),
                self.assertRaisesRegex(ReleaseRefused, reason),
            ):
                candidates(self.members, OWNER, given)

    def test_unreadable_registry_fails_closed(self) -> None:
        with self.assertRaises(ReleaseInfrastructureError):
            current_members("schema: something-else\n", OWNER, self.reader)


class ScheduleEnumerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeOrgApi()
        self.client = OrgClient(self.api, API)
        self.members = current_members(REGISTRY, OWNER, registry_module())

    def test_enumerates_declared_active_repositories_and_isolates_errors(self) -> None:
        found = discover(self.client, OWNER, self.members, strict=False)
        self.assertEqual(
            [f"{OWNER}/l9-constellation-topology", f"{OWNER}/l9-meta-injector"],
            [target.repository for target in found.targets],
        )
        self.assertEqual(1, len(found.errors))
        self.assertIn("flaky", found.errors[0])
        matrix = json.loads(found.outputs()["matrix"])["include"]
        self.assertEqual(
            {
                "repository": f"{OWNER}/l9-meta-injector",
                "name": "l9-meta-injector",
                "default_branch": "main",
            },
            matrix[1],
        )
        self.assertEqual("2", found.outputs()["count"])
        # The declaration is probed on each repository's own default branch.
        self.assertTrue(
            any(
                "undeclared/contents" in call and "ref=trunk" in call
                for call in self.api.calls
            )
        )


class DispatchPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeOrgApi()
        self.client = OrgClient(self.api, API)

    def test_dispatched_declared_repository(self) -> None:
        found = discover(self.client, OWNER, ["l9-meta-injector"], strict=True)
        self.assertEqual(
            [f"{OWNER}/l9-meta-injector"],
            [target.repository for target in found.targets],
        )
        self.assertEqual([], found.errors)

    def test_dispatch_is_strict(self) -> None:
        for name, error in (
            ("undeclared", ReleaseRefused),
            ("archived-one", ReleaseRefused),
            ("not-visible", ReleaseRefused),
            ("flaky", ReleaseInfrastructureError),
        ):
            with self.subTest(name=name), self.assertRaises(error):
                discover(self.client, OWNER, [name], strict=True)


class CommandLineTests(unittest.TestCase):
    def test_members_then_discover_write_workflow_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = pathlib.Path(tmp) / "registry.yaml"
            registry.write_text(REGISTRY, encoding="utf-8")
            outputs = pathlib.Path(tmp) / "out"
            with (
                mock.patch.dict(
                    os.environ, {"GITHUB_OUTPUT": str(outputs), "GITHUB_API_URL": API}
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    0,
                    run(
                        [
                            "members",
                            "--owner",
                            OWNER,
                            "--repository",
                            "l9-meta-injector",
                            "--registry-file",
                            str(registry),
                        ]
                    ),
                )
                self.assertEqual(
                    0,
                    run(
                        [
                            "discover",
                            "--owner",
                            OWNER,
                            "--members",
                            "l9-meta-injector",
                            "--strict",
                        ],
                        transport=FakeOrgApi(),
                    ),
                )
            lines = dict(
                line.split("=", 1)
                for line in outputs.read_text(encoding="utf-8").splitlines()
            )
        self.assertEqual("l9-meta-injector", lines["repositories"])
        self.assertEqual("1", lines["count"])
        self.assertEqual("[]", lines["errors"])
        self.assertEqual(
            f"{OWNER}/l9-meta-injector",
            json.loads(lines["matrix"])["include"][0]["repository"],
        )

    def test_member_names_are_validated(self) -> None:
        with self.assertRaises(ReleaseRefused):
            run(
                ["discover", "--owner", OWNER, "--members", "ok,../etc"],
                transport=FakeOrgApi(),
            )


if __name__ == "__main__":
    unittest.main()
