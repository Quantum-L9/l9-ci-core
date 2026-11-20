"""Load and check the settings declaration from a Quantum-L9/.github checkout.

The shape is owned by ``settings/settings.schema.json`` in that checkout; the
engine validates against that file and never carries a copy of it.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .errors import SettingsRefused

UNKNOWN = "UNKNOWN"
ORG_SCHEMA = "l9.github-settings.org/v1"
REPO_SCHEMA = "l9.github-settings.repo/v1"


@dataclass(frozen=True)
class Declaration:
    organization: str
    repositories: dict[str, dict[str, Any]]


def _load(path: pathlib.Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SettingsRefused(
            f"{path.name}: unreadable declaration: {error}"
        ) from error


def _validator(schema: dict[str, Any], kind: str) -> Draft202012Validator:
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{kind}"})


def _check(validator: Draft202012Validator, label: str, document: Any) -> None:
    errors = sorted(
        validator.iter_errors(document), key=lambda e: list(e.absolute_path)
    )
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise SettingsRefused(
            f"{label}: does not match settings.schema.json at {where}: "
            f"{first.message[:200]} ({len(errors)} error(s))"
        )


def repository_files(settings: pathlib.Path) -> list[pathlib.Path]:
    directory = settings / "repos"
    if not directory.is_dir():
        return []
    # iterdir, not glob: `.github.yml` is a dotfile.
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def load_declaration(settings: pathlib.Path, owner: str) -> Declaration:
    schema_path = settings / "settings.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SettingsRefused(f"settings.schema.json: unreadable: {error}") from error
    org = _load(settings / "org.yml")
    _check(_validator(schema, "org"), "org.yml", org)
    if org["organization"] != owner:
        raise SettingsRefused(
            f"org.yml declares {org['organization']!r}, the run is for {owner!r}"
        )
    repositories: dict[str, dict[str, Any]] = {}
    validator = _validator(schema, "repo")
    for path in repository_files(settings):
        document = _load(path)
        _check(validator, f"repos/{path.name}", document)
        name = document["repository"]
        if path.stem != name:
            raise SettingsRefused(f"repos/{path.name}: must be named {name}.yml")
        repositories[name] = document
    if not repositories:
        raise SettingsRefused("no repository declarations under repos/")
    return Declaration(organization=owner, repositories=repositories)


def select(declaration: Declaration, repository: str) -> dict[str, dict[str, Any]]:
    """All declared repositories, or exactly the one named (name or owner/name)."""
    if not repository:
        return dict(declaration.repositories)
    owner, _, name = repository.rpartition("/")
    if owner and owner != declaration.organization:
        raise SettingsRefused(f"{repository}: not in {declaration.organization}")
    if name not in declaration.repositories:
        raise SettingsRefused(f"{repository}: has no declaration under repos/")
    return {name: declaration.repositories[name]}
