"""Release engine CLI.

    python3 -m tools.l9_release [--workspace DIR] [--repository OWNER/NAME]
                                [--commit REV] [--remote NAME] STAGE [...]

    plan                                      decide pending work (read-only)
    build   --out DIR --scratch DIR --phases {verified-upstream,run}
    publish --dist DIR                        perform pending work, then verify
    verify  [--dist DIR]                      re-download and check everything

``plan`` writes its decision to ``$GITHUB_OUTPUT`` when set (``configured``
is ``false`` for a repository without ``.l9/release.json``, which is not an
error). Exit codes: ``0`` done, ``1`` refused by a precondition, ``2`` tool,
transport or contract failure.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections.abc import Sequence

from . import gitops
from .build import PHASE_MODES, build
from .config import ReleaseConfig, load_config, refuse_core_repository
from .errors import ReleaseInfrastructureError, ReleaseRefused
from .github import GitHubReleases, Transport, default_transport
from .plan import plan
from .publish import publish, verify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l9-release")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--remote", default="origin")
    stages = parser.add_subparsers(dest="stage", required=True)
    stages.add_parser("plan")
    build_stage = stages.add_parser("build")
    build_stage.add_argument("--out", type=pathlib.Path, required=True)
    build_stage.add_argument("--scratch", type=pathlib.Path, required=True)
    build_stage.add_argument("--phases", choices=PHASE_MODES, required=True)
    publish_stage = stages.add_parser("publish")
    publish_stage.add_argument("--dist", type=pathlib.Path, required=True)
    verify_stage = stages.add_parser("verify")
    verify_stage.add_argument("--dist", type=pathlib.Path)
    return parser


def _write_outputs(fields: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as stream:
        for key, value in fields.items():
            if "\n" in value:
                raise ReleaseInfrastructureError(f"output {key} spans lines")
            stream.write(f"{key}={value}\n")


def _releases(repository: str | None, transport: Transport | None) -> GitHubReleases:
    if not repository:
        raise ReleaseInfrastructureError(
            "--repository or GITHUB_REPOSITORY is required"
        )
    return GitHubReleases(repository, transport or default_transport())


def run(
    argv: Sequence[str] | None = None,
    transport: Transport | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    workspace: pathlib.Path = arguments.workspace.resolve()
    refuse_core_repository(arguments.repository)
    config: ReleaseConfig | None = load_config(workspace)
    if config is None:
        print("l9-release: no .l9/release.json; this repository does not release")
        if arguments.stage == "plan":
            _write_outputs({"configured": "false", "work_pending": "false"})
            return 0
        raise ReleaseRefused("no .l9/release.json")
    commit = gitops.resolve_commit(workspace, arguments.commit)

    if arguments.stage == "plan":
        decided = plan(
            workspace,
            config,
            commit,
            arguments.remote,
            _releases(arguments.repository, transport),
        )
        print(
            json.dumps(
                {"commit": commit, **decided.outputs()}, indent=2, sort_keys=True
            )
        )
        _write_outputs({"configured": "true", "commit": commit, **decided.outputs()})
    elif arguments.stage == "build":
        result = build(
            workspace,
            config,
            commit,
            arguments.out.resolve(),
            arguments.scratch.resolve(),
            arguments.phases,
        )
        for name, digest in result.artifacts.items():
            print(f"{digest}  {name}")
        print(
            f"build: {result.version} at {result.commit} reproducible across 2 rounds"
        )
    elif arguments.stage == "publish":
        publish(
            workspace,
            config,
            commit,
            arguments.dist.resolve(),
            arguments.remote,
            _releases(arguments.repository, transport),
        )
    else:
        verify(
            workspace,
            config,
            commit,
            arguments.remote,
            _releases(arguments.repository, transport),
            arguments.dist.resolve() if arguments.dist else None,
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except ReleaseRefused as error:
        print(f"l9-release: refused: {error}", file=sys.stderr)
        return 1
    except ReleaseInfrastructureError as error:
        print(f"l9-release: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
