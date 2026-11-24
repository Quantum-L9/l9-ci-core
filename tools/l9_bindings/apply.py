"""Writing a resolved identity where the binding says it belongs.

Two destinations, two mechanisms:

* **library** — the declared ``refresh`` command moves the lock. The engine
  never guesses how a repository builds its lock; the command is in the
  binding, so it is reviewable and identical in every repository.
* **contract** — a JSON document with named fields. Only the declared fields
  are written, a missing path is a hard error (never invented), and the file
  is re-serialized with a stable key order so the diff is exactly the change.

Nothing here decides *whether* to write. That is ``converge``'s job, and the
policy it enforces lives in the binding.
"""

from __future__ import annotations

import fnmatch
import json
import pathlib
import re
import shlex
import subprocess
from typing import Any

from .model import Binding


class ApplyError(Exception):
    """The declared destination cannot be written exactly as declared."""


def read_document(path: pathlib.Path) -> dict[str, Any]:
    if path.suffix != ".json":
        raise ApplyError(
            f"{path} is not JSON; a contract binding writes named JSON fields, "
            "and a YAML destination would lose the comments that carry its "
            "invariants"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplyError(f"{path} is not readable JSON: {error}") from error
    if not isinstance(document, dict):
        raise ApplyError(f"{path} is not a JSON object")
    return document


def get_dotted(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ApplyError(f"{path} does not exist in the document")
        current = current[part]
    return current


def set_dotted(document: dict[str, Any], path: str, value: Any) -> bool:
    """Set an existing path; return whether the value changed."""
    parts = path.split(".")
    current: Any = document
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise ApplyError(f"{path} does not exist in the document")
        current = current[part]
    leaf = parts[-1]
    if not isinstance(current, dict) or leaf not in current:
        raise ApplyError(f"{path} does not exist in the document")
    if current[leaf] == value:
        return False
    current[leaf] = value
    return True


def digest_for(binding: Binding, files: dict[str, str]) -> str:
    if not files:
        raise ApplyError(
            f"binding {binding.id}: the release publishes no digest to pin"
        )
    if binding.artifact:
        matches = sorted(
            name for name in files if fnmatch.fnmatchcase(name, binding.artifact)
        )
        if len(matches) != 1:
            raise ApplyError(
                f"binding {binding.id}: artifact {binding.artifact!r} matches "
                f"{len(matches)} published file(s) {matches}"
            )
        return files[matches[0]]
    if len(files) == 1:
        return next(iter(files.values()))
    raise ApplyError(
        f"binding {binding.id}: the release publishes {len(files)} files, so "
        "the binding must name which one its digest field pins (artifact:)"
    )


def render(template: str, values: dict[str, str]) -> str:
    rendered = template
    for name, value in values.items():
        rendered = rendered.replace("{" + name + "}", value)
    unresolved = sorted(set(re.findall(r"\{([a-z_]+)\}", rendered)))
    if unresolved:
        raise ApplyError(
            f"template names {unresolved} but no value was resolved for them"
        )
    return rendered


def rewrite(
    workspace: pathlib.Path, binding: Binding, values: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Apply the declared fields; return ``(before, after)`` for the PR body."""
    if binding.file is None:
        raise ApplyError(f"binding {binding.id} names no file")
    path = workspace / binding.file
    document = read_document(path)
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    changed = False
    for name, field in binding.fields.items():
        previous: list[str] = []
        for destination in field.paths:
            try:
                previous.append(str(get_dotted(document, destination)))
            except ApplyError:
                previous.append("<absent>")
        before[name] = ", ".join(dict.fromkeys(previous))
        if field.template is not None:
            value = render(field.template, values)
        else:
            if name not in values:
                raise ApplyError(
                    f"binding {binding.id} declares field {name!r} but no value "
                    "was resolved for it"
                )
            value = values[name]
        for destination in field.paths:
            changed = set_dotted(document, destination, value) or changed
        after[name] = value
    if changed:
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return before, after


def refresh(workspace: pathlib.Path, binding: Binding, version: str) -> str:
    """Run the declared refresh command; return it for the transcript."""
    if binding.refresh is None or binding.lock is None or binding.package is None:
        raise ApplyError(f"binding {binding.id} is not a library binding")
    command = render(
        binding.refresh,
        {
            "package": binding.package,
            "version": version,
            "lock": binding.lock,
        },
    )
    argv = shlex.split(command)
    result = subprocess.run(
        argv, cwd=workspace, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ApplyError(
            f"binding {binding.id}: {command!r} failed with "
            f"{result.returncode}: {(result.stderr or result.stdout).strip()[:800]}"
        )
    return command
