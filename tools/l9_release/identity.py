"""Release identity: strict SemVer core, version authority, CHANGELOG section.

The version grammar is the SemVer 2.0.0 core with no leading zeros, the same
grammar l9-meta-injector adopted in its ADR-075 (``4.02.0`` would otherwise
alias ``4.2.0``). Prerelease and build metadata are refused: an exact release
tag is ``vMAJOR.MINOR.PATCH`` and nothing else, and the maintained major tag is
``vMAJOR``.
"""

from __future__ import annotations

import datetime
import json
import re
import tomllib
from dataclasses import dataclass

from .errors import ReleaseRefused

NUMERIC = r"(?:0|[1-9][0-9]*)"
VERSION = re.compile(rf"^({NUMERIC})\.({NUMERIC})\.({NUMERIC})$")
EXACT_TAG = re.compile(rf"^v({NUMERIC})\.({NUMERIC})\.({NUMERIC})$")
MAJOR_TAG = re.compile(rf"^v({NUMERIC})$")

#: ``## 3.0.0 - 2026-11-14`` and ``## [3.0.0] - 2026-11-14`` (Keep a Changelog).
#: The date is mandatory: an undated or "Unreleased" heading is not a release.
CHANGELOG_HEADING = re.compile(
    r"^##\s+\[?(?P<version>[0-9][0-9.]*)\]?\s+[-–—]\s+"
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\b"
)
ANY_SECTION = re.compile(r"^##\s")


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(value)
    if match is None:
        raise ReleaseRefused(
            f"version {value!r} is not a plain SemVer core (MAJOR.MINOR.PATCH, "
            "no leading zeros, no prerelease or build metadata)"
        )
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def exact_tag_version(tag: str) -> tuple[int, int, int] | None:
    match = EXACT_TAG.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


@dataclass(frozen=True)
class ReleaseIdentity:
    version: str
    major: int
    exact_tag: str
    major_tag: str

    @property
    def key(self) -> tuple[int, int, int]:
        return parse_version(self.version)


def release_identity(version: str) -> ReleaseIdentity:
    major, _, _ = parse_version(version)
    return ReleaseIdentity(
        version=version,
        major=major,
        exact_tag=f"v{version}",
        major_tag=f"v{major}",
    )


def read_version(authority: str, text: str) -> str:
    """Read the version from the bytes of the declared version authority."""

    if authority == "pyproject.toml":
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise ReleaseRefused(
                f"pyproject.toml is not valid TOML: {error}"
            ) from error
        project = document.get("project")
        version = project.get("version") if isinstance(project, dict) else None
        dynamic = project.get("dynamic", []) if isinstance(project, dict) else []
        if isinstance(dynamic, list) and "version" in dynamic:
            raise ReleaseRefused(
                "pyproject.toml declares a dynamic version; the release engine "
                "needs a static [project].version as its version authority"
            )
    elif authority == "package.json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise ReleaseRefused(f"package.json is not valid JSON: {error}") from error
        version = document.get("version") if isinstance(document, dict) else None
    else:
        raise ReleaseRefused(f"unsupported version authority {authority!r}")
    if not isinstance(version, str):
        raise ReleaseRefused(f"{authority} declares no version")
    parse_version(version)
    return version


@dataclass(frozen=True)
class ChangelogSection:
    version: str
    date: str
    body: str


def changelog_section(text: str, version: str) -> ChangelogSection:
    """Return the dated section for ``version``; refuse when there is none."""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = CHANGELOG_HEADING.match(line)
        if match is None or match.group("version") != version:
            continue
        date = match.group("date")
        try:
            datetime.date.fromisoformat(date)
        except ValueError as error:
            raise ReleaseRefused(
                f"CHANGELOG section for {version} has an invalid date {date!r}"
            ) from error
        end = next(
            (
                position
                for position in range(index + 1, len(lines))
                if ANY_SECTION.match(lines[position])
            ),
            len(lines),
        )
        body = "\n".join(lines[index + 1 : end]).strip("\n")
        return ChangelogSection(version=version, date=date, body=body)
    raise ReleaseRefused(
        f"no dated CHANGELOG section '## {version} - YYYY-MM-DD' for this release"
    )
