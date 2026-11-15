"""Release runner discovery: which repositories does today's run release?

The org-CI required workflow runs only on ``pull_request`` in consumer
repositories (operator evidence 2026-11-15: topology PR #66 merged with no
``push`` run), so releasing on push inside it is impossible. Releases are run
instead by ``.github/workflows/release-runner.yml`` in Core: once a day on
``schedule`` and on ``workflow_dispatch``. This module decides its targets:

``schedule``  every ``lifecycle: current`` member of the canonical L9 repository
              registry (``Quantum-L9/.github`` ``semantics/repository_registry.yaml``,
              read by Core's one registry reader) that is not archived or
              disabled, is not Core itself, and carries ``.l9/release.json`` on
              its default branch.
``dispatch``  exactly the one named repository, which must be a current member
              and carry the declaration; anything else is refused.

Reading candidates from the registry first lets the workflow mint its
discovery token for exactly that list of repositories instead of the App's
whole installation.

Discovery never fails as a whole because one repository's probe failed: that
repository is reported in ``errors`` and the rest are released. The workflow's
final job fails when ``errors`` is non-empty, so nothing is silently skipped.
Each target then gets its own job, its own one-repository installation token,
and its own concurrency group.

    python3 -m tools.l9_release.runner members --owner ORG [--repository NAME]
    python3 -m tools.l9_release.runner discover --owner ORG --members a,b [--strict]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import sys
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import ModuleType

from .config import CONFIG_PATH, REFUSED_REPOSITORIES
from .errors import ReleaseInfrastructureError, ReleaseRefused
from .github import Transport, default_transport

CORE_ROOT = pathlib.Path(__file__).resolve().parents[2]
NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PAGE_SIZE = 100
MAX_PAGES = 50


@dataclass(frozen=True)
class Target:
    repository: str
    default_branch: str


@dataclass
class Discovery:
    targets: list[Target] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def outputs(self) -> dict[str, str]:
        matrix = [
            {
                "repository": t.repository,
                "name": t.repository.split("/", 1)[1],
                "default_branch": t.default_branch,
            }
            for t in self.targets
        ]
        return {
            "matrix": json.dumps({"include": matrix}, separators=(",", ":")),
            "count": str(len(matrix)),
            "errors": json.dumps(self.errors, separators=(",", ":")),
        }


class OrgClient:
    def __init__(self, transport: Transport, api_url: str | None = None) -> None:
        self.transport = transport
        self.api = (
            api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com"
        ).rstrip("/")

    def _get(self, path: str) -> tuple[int, object]:
        response = self.transport.request("GET", f"{self.api}{path}")
        if response.status == 404:
            return 404, None
        if response.status != 200:
            raise ReleaseInfrastructureError(f"GET {path}: HTTP {response.status}")
        return 200, response.json()

    def repository(self, full_name: str) -> dict[str, object] | None:
        status, document = self._get(f"/repos/{full_name}")
        if status == 404:
            return None
        if not isinstance(document, dict):
            raise ReleaseInfrastructureError(f"{full_name}: malformed repository")
        return document

    def has_declaration(self, full_name: str, ref: str) -> bool:
        query = urllib.parse.urlencode({"ref": ref})
        status, _ = self._get(f"/repos/{full_name}/contents/{CONFIG_PATH}?{query}")
        return status == 200


def _probe(item: dict[str, object], owner: str) -> Target | None:
    full_name = item.get("full_name")
    branch = item.get("default_branch")
    if not isinstance(full_name, str) or not isinstance(branch, str) or not branch:
        raise ReleaseInfrastructureError(f"repository entry is malformed: {item!r}")
    if full_name.split("/", 1)[0].lower() != owner.lower():
        return None
    if item.get("archived") or item.get("disabled"):
        return None
    if full_name.lower() in REFUSED_REPOSITORIES:
        return None
    return Target(repository=full_name, default_branch=branch)


def registry_module() -> ModuleType:
    """Core's one registry reader (run-repository-verification, AGENTS.md §24)."""

    path = CORE_ROOT / ".github/actions/run-repository-verification/run.py"
    spec = importlib.util.spec_from_file_location("l9_registry_reader", path)
    if spec is None or spec.loader is None:
        raise ReleaseInfrastructureError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_members(registry_text: str, owner: str, reader: ModuleType) -> list[str]:
    """Names of the owner's ``lifecycle: current`` registry members, Core excluded."""

    try:
        members = reader.parse_registry_members(registry_text)
    except reader.RegistryError as error:
        raise ReleaseInfrastructureError(f"registry unreadable: {error}") from error
    names = sorted(
        {
            coordinate.split("/", 1)[1]
            for coordinate, lifecycle in members
            if lifecycle == "current"
            and coordinate.split("/", 1)[0].lower() == owner.lower()
            and coordinate.lower() not in REFUSED_REPOSITORIES
        },
        key=str.lower,
    )
    if not names:
        raise ReleaseInfrastructureError(f"registry lists no current {owner} member")
    return names


def candidates(members: list[str], owner: str, dispatched: str | None) -> list[str]:
    """The repositories a run may touch: every member, or the one dispatched."""

    if not NAME.fullmatch(owner):
        raise ReleaseRefused(f"invalid owner {owner!r}")
    if not dispatched:
        return members
    name = dispatched.strip()
    if "/" in name:
        given_owner, _, name = name.partition("/")
        if given_owner.lower() != owner.lower():
            raise ReleaseRefused(f"{dispatched!r} is not a repository of {owner}")
    if not NAME.fullmatch(name):
        raise ReleaseRefused(f"invalid repository name {dispatched!r}")
    if f"{owner}/{name}".lower() in REFUSED_REPOSITORIES:
        raise ReleaseRefused(
            f"{owner}/{name} releases only through docs/release/tag-and-release.sh"
        )
    match = [member for member in members if member.lower() == name.lower()]
    if not match:
        raise ReleaseRefused(
            f"{owner}/{name} is not a current member of the L9 repository registry"
        )
    return match


def discover(
    client: OrgClient, owner: str, names: list[str], strict: bool
) -> Discovery:
    """Probe each candidate for ``.l9/release.json`` on its default branch.

    ``strict`` (a dispatched release) refuses a candidate that is missing,
    archived, or undeclared and lets a probe error fail the run. Otherwise a
    probe error is recorded against that repository and the rest continue.
    """

    result = Discovery()
    for name in names:
        full_name = f"{owner}/{name}"
        try:
            document = client.repository(full_name)
            target = _probe(document, owner) if document is not None else None
            if target is None:
                if strict:
                    raise ReleaseRefused(
                        f"{full_name} is not visible, or is archived or disabled"
                    )
                continue
            if client.has_declaration(full_name, target.default_branch):
                result.targets.append(target)
            elif strict:
                raise ReleaseRefused(
                    f"{full_name} has no {CONFIG_PATH} on {target.default_branch}"
                )
        except ReleaseInfrastructureError as error:
            if strict:
                raise
            result.errors.append(f"{full_name}: {error}")
    return result


def _write_outputs(fields: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as stream:
        for key, value in fields.items():
            stream.write(f"{key}={value}\n")


def run(argv: Sequence[str] | None = None, transport: Transport | None = None) -> int:
    parser = argparse.ArgumentParser(prog="l9-release-runner")
    commands = parser.add_subparsers(dest="command", required=True)
    members = commands.add_parser("members")
    members.add_argument("--owner", required=True)
    members.add_argument("--repository", default="")
    members.add_argument("--registry-file", type=pathlib.Path)
    find = commands.add_parser("discover")
    find.add_argument("--owner", required=True)
    find.add_argument("--members", required=True)
    find.add_argument("--strict", action="store_true")
    arguments = parser.parse_args(argv)

    if arguments.command == "members":
        reader = registry_module()
        if arguments.registry_file is not None:
            text = arguments.registry_file.read_text(encoding="utf-8")
        else:
            try:
                text = reader.fetch_registry()
            except reader.RegistryError as error:
                raise ReleaseInfrastructureError(str(error)) from error
        chosen = candidates(
            current_members(text, arguments.owner, reader),
            arguments.owner,
            arguments.repository or None,
        )
        print("candidates: " + ", ".join(chosen))
        _write_outputs({"repositories": ",".join(chosen), "count": str(len(chosen))})
        return 0

    names = [name for name in arguments.members.split(",") if name]
    for name in names:
        if not NAME.fullmatch(name):
            raise ReleaseRefused(f"invalid member name {name!r}")
    client = OrgClient(transport or default_transport())
    found = discover(client, arguments.owner, names, arguments.strict)
    for target in found.targets:
        print(f"release target: {target.repository} ({target.default_branch})")
    for problem in found.errors:
        print(f"::error::release discovery: {problem}")
    _write_outputs(found.outputs())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except ReleaseRefused as error:
        print(f"l9-release-runner: refused: {error}", file=sys.stderr)
        return 1
    except ReleaseInfrastructureError as error:
        print(f"l9-release-runner: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
