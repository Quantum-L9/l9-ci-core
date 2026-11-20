"""Centrally resolved configuration for a stamped repository, with a digest.

A repository whose stamp record (``l9.core-stamp/v2``) declares paths as
*inherited* does not carry those files. Their bytes are whatever this Core
revision renders, selected at runtime. ``resolve`` returns those bytes with one
digest per file and one digest over the whole set, so a run can state exactly
which configuration it used. ``materialize`` writes them into a workspace for
the tools that read their configuration from disk.

Nothing here reads a consumer value to decide content: the profile and the
inherited paths come from the validated stamp record, and every byte comes
from Core. A consumer file that would change an inherited configuration is
refused, never merged.

Standard library only: the admission bridge imports this from a pinned Core
checkout with no third-party packages.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contract import ContractError
from .stamp import (
    CORE_REPOSITORY,
    inherited_paths,
    load_stamp_record,
    render_file,
)

EVIDENCE_SCHEMA = "l9.core-inherited-config/v1"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Resolution:
    """The configuration Core resolved for one stamped repository."""

    profile: str
    files: Mapping[str, bytes]

    @property
    def digests(self) -> dict[str, str]:
        return {
            relative: _digest(data) for relative, data in sorted(self.files.items())
        }

    @property
    def configuration_digest(self) -> str:
        """One digest over every resolved path and its content digest."""
        canonical = json.dumps(self.digests, sort_keys=True, separators=(",", ":"))
        return _digest(canonical.encode("utf-8"))

    def evidence(self, core_revision: str = "") -> dict[str, Any]:
        core: dict[str, str] = {"repository": CORE_REPOSITORY}
        if core_revision:
            core["revision"] = core_revision
        return {
            "schema": EVIDENCE_SCHEMA,
            "core": core,
            "profile": self.profile,
            "configuration_digest": self.configuration_digest,
            "files": self.digests,
        }


def resolve(root: pathlib.Path) -> Resolution | None:
    """Resolve the configuration ``root``'s stamp record inherits from Core.

    Returns ``None`` when the repository carries no stamp record or inherits
    nothing. A malformed record raises ``ContractError``.
    """
    record = load_stamp_record(root.resolve())
    if record is None:
        return None
    inherited = inherited_paths(record)
    if not inherited:
        return None
    return Resolution(
        profile=record["profile"],
        files={relative: render_file(relative) for relative in inherited},
    )


def materialize(root: pathlib.Path, resolution: Resolution) -> list[str]:
    """Write each resolved file that ``root`` does not hold; return those paths.

    A file already holding Core's bytes is left alone. A file holding other
    bytes is a consumer override and is refused, as is a symlink anywhere on
    the path: the resolved configuration is never merged with local content.
    """
    root = root.resolve()
    written: list[str] = []
    for relative, data in sorted(resolution.files.items()):
        path = root / relative
        for parent in [path, *path.parents]:
            if parent == root:
                break
            if parent.is_symlink():
                raise ContractError(
                    f"refusing to materialize through a symlink: "
                    f"{parent.relative_to(root)}"
                )
        if path.exists():
            if not path.is_file() or path.read_bytes() != data:
                raise ContractError(
                    f"{relative}: local copy overrides configuration inherited "
                    f"from Core (central {_digest(data)}); remove it"
                )
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(relative)
    return written
