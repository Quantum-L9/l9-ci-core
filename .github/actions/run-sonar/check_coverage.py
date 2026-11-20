"""Admit the coverage reports a Core Sonar scan will import, or fail closed.

The Sonar step reuses the ``coverage.xml`` the consumer's own test run already
wrote (the Core #289 pattern): there is no second test run here, and this module
runs no repository code. It only proves that what Sonar is about to import is
real, because a scan without its report reads as 0% coverage for the code it
measures — a green dashboard that measured nothing (CI_CONFIG_FINDINGS row 6).

A report is admitted only when it is:

* a workspace-relative path made of ``[A-Za-z0-9._/-]`` characters, with no
  ``..`` segment, that resolves inside the workspace (no symlink escape);
* a regular, non-empty file;
* well-formed XML whose root element is Cobertura's ``coverage``.

The token is checked for presence only and never printed: a blank token fails
here, with its name, instead of as an opaque scanner error.

    python3 check_coverage.py --reports "coverage.xml,cov/other.xml" [--base-dir DIR] [--workspace DIR]

Report paths are relative to ``--base-dir`` (the scanner's ``projectBaseDir``),
which must itself be a plain path inside ``--workspace`` (default: the current
directory, which is ``GITHUB_WORKSPACE`` in a composite step).

On success prints the admitted paths, comma-joined, on stdout and exits ``0``.
Exit ``1`` is a missing, empty or malformed report or a blank token; exit ``2``
is an invalid invocation or a path outside the workspace.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
TOKEN_ENV = "SONAR_TOKEN"


class Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def split_reports(raw: str) -> list[str]:
    reports = [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    if not reports:
        raise Refused(2, "no coverage report named; pass at least one path")
    if len(set(reports)) != len(reports):
        raise Refused(2, f"a coverage report is named twice: {reports}")
    return reports


def contained(relative: str, root: Path, workspace: Path, what: str) -> Path:
    if not SAFE_PATH.match(relative) or relative.startswith("/"):
        raise Refused(2, f"{what} {relative!r} is not a plain workspace path")
    if ".." in Path(relative).parts:
        raise Refused(2, f"{what} {relative!r} leaves the workspace")
    path = root / relative
    try:
        path.resolve(strict=False).relative_to(workspace.resolve())
    except ValueError as exc:
        raise Refused(2, f"{what} {relative!r} resolves outside the workspace") from exc
    return path


def admit(report: str, base: Path, workspace: Path) -> str:
    path = contained(report, base, workspace, "coverage report path")
    if not path.is_file():
        raise Refused(
            1, f"coverage report {report} is missing: the test run did not hand it over"
        )
    if path.stat().st_size == 0:
        raise Refused(1, f"coverage report {report} is empty")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise Refused(
            1, f"coverage report {report} is not well-formed XML: {exc}"
        ) from exc
    if root.tag != "coverage":
        raise Refused(
            1,
            f"coverage report {report} is not Cobertura XML (root element <{root.tag}>)",
        )
    return report


def run(
    reports_arg: str, base_dir: str, workspace: Path, environ: dict[str, str]
) -> str:
    if not environ.get(TOKEN_ENV, "").strip():
        raise Refused(
            1,
            f"{TOKEN_ENV} is blank: the caller must pass the organization token "
            "(a fork pull request has none; skip the step there instead)",
        )
    base = contained(base_dir, workspace, workspace, "project base directory")
    if not base.is_dir():
        raise Refused(2, f"project base directory {base_dir!r} does not exist")
    return ",".join(
        admit(report, base, workspace) for report in split_reports(reports_arg)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports", required=True)
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        admitted = run(args.reports, args.base_dir, args.workspace, dict(os.environ))
    except Refused as exc:
        print(f"::error title=Core Sonar step::{exc}", file=sys.stderr)
        return exc.code
    print(admitted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
