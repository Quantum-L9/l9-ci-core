"""Settings engine CLI.

    python3 -m tools.l9_settings --settings DIR [--owner ORG] [--repository NAME]
                                 STAGE [--out FILE]

    repositories   print the declared repository names (comma-separated), and
                   write `repositories=` to $GITHUB_OUTPUT when set
    plan           read live state and print the plan (never writes)
    apply          plan again from live state, then write the changes

The plan is printed, written to ``--out`` as JSON, and appended to
``$GITHUB_STEP_SUMMARY`` when set. Exit codes: ``0`` clean (plan: nothing
unreadable and no secret missing), ``1`` refused by the declaration or a
declared secret is missing, ``2`` a declared setting is unreadable (nothing is
applied) or a transport/write failure.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
from collections.abc import Sequence

from .declaration import load_declaration, select
from .engine import Api, Plan, api_from_environment, apply_plan, plan_settings
from .errors import SettingsInfrastructureError, SettingsRefused


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l9-settings")
    parser.add_argument("--settings", type=pathlib.Path, required=True)
    parser.add_argument(
        "--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "Quantum-L9")
    )
    parser.add_argument("--repository", default="")
    stages = parser.add_subparsers(dest="stage", required=True)
    stages.add_parser("repositories")
    for stage in ("plan", "apply"):
        sub = stages.add_parser(stage)
        sub.add_argument("--out", type=pathlib.Path)
    return parser


def _append(variable: str, text: str) -> None:
    target = os.environ.get(variable)
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(text)


def render(plan: Plan, stage: str) -> str:
    lines = [f"## Settings {stage}", ""]
    if plan.changes:
        lines += ["```", *[c.line() for c in plan.changes], "```"]
    else:
        lines.append("No change: live state matches every declared setting.")
    for title, items in (
        ("Unreadable (fail closed: nothing is applied)", plan.unreadable),
        ("Cannot be applied by settings", plan.unappliable),
        ("Notes", plan.notes),
    ):
        if items:
            lines += ["", f"### {title}", *[f"- {i}" for i in items]]
    return "\n".join(lines) + "\n"


def confined_output(path: pathlib.Path) -> pathlib.Path:
    """``--out`` resolved, refused unless it lies under the working or temp directory."""
    resolved = pathlib.Path(os.path.realpath(path))
    roots = [pathlib.Path.cwd(), pathlib.Path(tempfile.gettempdir())]
    runner_temp = os.environ.get("RUNNER_TEMP", "")
    if runner_temp:
        roots.append(pathlib.Path(runner_temp))
    for root in roots:
        base = pathlib.Path(os.path.realpath(root))
        if resolved == base or resolved.is_relative_to(base):
            return resolved
    raise SettingsRefused(f"--out {path} is outside the working and temp directories")


def run(argv: Sequence[str] | None = None, api: Api | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    declaration = load_declaration(arguments.settings.resolve(), arguments.owner)
    repositories = select(declaration, arguments.repository)
    if arguments.stage == "repositories":
        names = ",".join(sorted(repositories))
        print(names)
        _append("GITHUB_OUTPUT", f"repositories={names}\n")
        return 0
    client = api or api_from_environment()
    plan = plan_settings(client, arguments.owner, repositories)
    report = render(plan, arguments.stage)
    print(report)
    _append("GITHUB_STEP_SUMMARY", report)
    if arguments.out:
        confined_output(arguments.out).write_text(
            json.dumps(plan.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if plan.unreadable:
        print(
            "l9-settings: fail closed: a declared setting is unreadable; nothing is applied",
            file=sys.stderr,
        )
        return 2
    if arguments.stage == "apply":
        failures = apply_plan(client, plan)
        for failure in failures:
            print(f"l9-settings: write failed: {failure}", file=sys.stderr)
        if failures:
            return 2
    if plan.unappliable:
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except SettingsRefused as error:
        print(f"l9-settings: refused: {error}", file=sys.stderr)
        return 1
    except SettingsInfrastructureError as error:
        print(f"l9-settings: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
