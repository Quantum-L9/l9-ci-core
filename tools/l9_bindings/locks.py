"""Reading a consumer's current pin out of its lock file.

The converge engine never runs a resolver to decide whether a consumer is
behind: a resolver answers "what would I install", which is a different
question from "what is pinned". These readers answer the second one directly
and fail closed on a lock they cannot parse, because an unreadable lock would
otherwise be reported as converged.
"""

from __future__ import annotations

import json
import pathlib
import re
import tomllib

_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)\s*==\s*(?P<version>[^\s;\\]+)")


class LockUnreadable(Exception):
    """The lock exists but its pin cannot be established."""


def normalise(name: str) -> str:
    """PEP 503 normalization, so ``l9_ci_sdk`` and ``l9-ci-sdk`` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def kind(path: pathlib.Path) -> str:
    if path.name == "uv.lock":
        return "uv"
    if path.name in {"package-lock.json", "npm-shrinkwrap.json"}:
        return "npm"
    return "requirements"


def pinned(workspace: pathlib.Path, lock: str, package: str) -> str | None:
    path = workspace / lock
    if not path.is_file():
        raise LockUnreadable(f"{lock} is declared but absent")
    flavour = kind(path)
    if flavour == "uv":
        return _uv(path, package)
    if flavour == "npm":
        return _npm(path, package)
    return _requirements(path, package)


def _uv(path: pathlib.Path, package: str) -> str | None:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LockUnreadable(f"{path} is not readable TOML: {error}") from error
    entries = document.get("package")
    if not isinstance(entries, list):
        raise LockUnreadable(f"{path} has no [[package]] table")
    wanted = normalise(package)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if isinstance(name, str) and normalise(name) == wanted:
            if not isinstance(version, str):
                raise LockUnreadable(f"{path} pins {name} without a version")
            return version
    return None


def _npm(path: pathlib.Path, package: str) -> str | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LockUnreadable(f"{path} is not readable JSON: {error}") from error
    packages = document.get("packages")
    if isinstance(packages, dict):
        entry = packages.get(f"node_modules/{package}")
        if isinstance(entry, dict) and isinstance(entry.get("version"), str):
            return entry["version"]
    dependencies = document.get("dependencies")
    if isinstance(dependencies, dict):
        entry = dependencies.get(package)
        if isinstance(entry, dict) and isinstance(entry.get("version"), str):
            return entry["version"]
    return None


def _requirements(path: pathlib.Path, package: str) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LockUnreadable(f"{path} is not readable text: {error}") from error
    wanted = normalise(package)
    for line in text.splitlines():
        match = _REQUIREMENT.match(line.strip())
        if match and normalise(match.group("name")) == wanted:
            return match.group("version")
    return None
