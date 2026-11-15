"""``.l9/release.json`` — the ``l9.release/v1`` repository release declaration.

A repository describes *what* it releases; Core decides *how*. The document is
closed: every field is required and an unknown field fails closed, exactly
like ``.l9/ci.json``. It carries no credentials, no workflow, no tool
versions, no commands, and no Core revision.

.. code-block:: json

    {
      "schema": "l9.release/v1",
      "adapter": "python-uv",
      "version_authority": "pyproject.toml",
      "changelog": "CHANGELOG.md",
      "major_tag": false
    }

``adapter`` selects the Core build adapter. ``version_authority`` is a
consistency assertion: it must name the one file that adapter reads the
version from (``pyproject.toml`` ``[project].version`` for ``python-uv``,
``package.json`` ``version`` for ``node-npm``), so a repository cannot point
the engine at a second version source. ``changelog`` is a repository-relative
path. ``major_tag`` opts into advancing the maintained ``vMAJOR`` tag, forward
only. ``release.schema.json`` beside this module is the published JSON Schema
of the same contract; ``tests/release_engine`` proves the two agree.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any

from .errors import ReleaseRefused

SCHEMA = "l9.release/v1"
CONFIG_PATH = pathlib.PurePosixPath(".l9/release.json")
SCHEMA_PATH = pathlib.Path(__file__).with_name("release.schema.json")

#: adapter -> the only file it reads the version from.
ADAPTER_VERSION_AUTHORITY = {
    "python-uv": "pyproject.toml",
    "node-npm": "package.json",
}
FIELDS = ("schema", "adapter", "version_authority", "changelog", "major_tag")

#: Core releases itself through docs/release/tag-and-release.sh, the single
#: authorized writer of Core's exact namespace (.l9/release-plane.yaml
#: release_writers). The engine must never become a second writer there.
REFUSED_REPOSITORIES = frozenset({"quantum-l9/l9-ci-core"})


@dataclass(frozen=True)
class ReleaseConfig:
    adapter: str
    version_authority: str
    changelog: str
    major_tag: bool


def _relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseRefused(f"{CONFIG_PATH}: {field} must be a non-empty string")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ReleaseRefused(
            f"{CONFIG_PATH}: {field} must be a repository-relative path: {value!r}"
        )
    return str(path)


def parse_config(document: Any) -> ReleaseConfig:
    """Validate an ``l9.release/v1`` document; fail closed on anything else."""

    if not isinstance(document, dict):
        raise ReleaseRefused(f"{CONFIG_PATH} must be a JSON object")
    unknown = sorted(set(document) - set(FIELDS))
    if unknown:
        raise ReleaseRefused(f"{CONFIG_PATH}: unknown field(s): {', '.join(unknown)}")
    missing = [field for field in FIELDS if field not in document]
    if missing:
        raise ReleaseRefused(f"{CONFIG_PATH}: missing field(s): {', '.join(missing)}")
    if document["schema"] != SCHEMA:
        raise ReleaseRefused(
            f"{CONFIG_PATH}: schema must be {SCHEMA!r}, got {document['schema']!r}"
        )
    adapter = document["adapter"]
    if adapter not in ADAPTER_VERSION_AUTHORITY:
        raise ReleaseRefused(
            f"{CONFIG_PATH}: adapter must be one of "
            f"{sorted(ADAPTER_VERSION_AUTHORITY)}, got {adapter!r}"
        )
    authority = document["version_authority"]
    if authority != ADAPTER_VERSION_AUTHORITY[adapter]:
        raise ReleaseRefused(
            f"{CONFIG_PATH}: adapter {adapter!r} reads its version from "
            f"{ADAPTER_VERSION_AUTHORITY[adapter]!r}; version_authority is "
            f"{authority!r}"
        )
    major_tag = document["major_tag"]
    if not isinstance(major_tag, bool):
        raise ReleaseRefused(f"{CONFIG_PATH}: major_tag must be a boolean")
    return ReleaseConfig(
        adapter=adapter,
        version_authority=authority,
        changelog=_relative_path(document["changelog"], "changelog"),
        major_tag=major_tag,
    )


def load_config(workspace: pathlib.Path) -> ReleaseConfig | None:
    """Return the declaration, or ``None`` when the repository has none."""

    path = workspace / CONFIG_PATH
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseRefused(f"{CONFIG_PATH} is not readable JSON: {error}") from error
    return parse_config(document)


def refuse_core_repository(repository: str | None) -> None:
    if repository and repository.lower() in REFUSED_REPOSITORIES:
        raise ReleaseRefused(
            f"{repository} releases only through docs/release/tag-and-release.sh "
            "(.l9/release-plane.yaml release_writers); the engine refuses it"
        )
