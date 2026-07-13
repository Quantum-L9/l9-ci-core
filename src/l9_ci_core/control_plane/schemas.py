"""Canonical control-plane JSON schema registry.

Maps stable logical schema names to the files under ``schemas/`` and provides
loaders. Only the standard library is used, so importing this module never
requires ``jsonschema``; validation (which does need ``jsonschema``) is layered
on top by the stages that perform it.

The schema directory is resolved relative to this file. Under the supported
editable install (``pip install --no-deps -e .``) that resolves to the
repository's ``schemas/`` directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "SCHEMA_DIR",
    "SCHEMAS",
    "schema_path",
    "load_schema",
    "iter_schema_names",
]

# schemas.py -> control_plane -> l9_ci_core -> src -> <repo root>
SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"

# Stable logical name -> filename. These are the canonical control-plane
# schemas introduced by the PR-B series.
SCHEMAS: dict[str, str] = {
    "event-context": "event-context.schema.json",
    "changed-files": "changed-files.schema.json",
    "gate-registry": "gate-registry.schema.json",
    "risk-tiers": "risk-tiers.schema.json",
    "gate-plan": "gate-plan.schema.json",
    "gate-result": "gate-result.schema.json",
    "legacy-job-results": "legacy-job-results.schema.json",
    "promotion-decision": "promotion-decision.schema.json",
    "control-plane-migration": "control-plane-migration.schema.json",
}


def iter_schema_names():
    """Yield the stable logical schema names, sorted for determinism."""
    yield from sorted(SCHEMAS)


def schema_path(name: str) -> Path:
    """Return the filesystem path for the schema registered under ``name``.

    Raises ``KeyError`` for an unknown name and ``FileNotFoundError`` if the
    registered file is absent. A missing schema file is always fatal for the
    control plane -- it is never silently skipped.
    """
    try:
        filename = SCHEMAS[name]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"unknown control-plane schema: {name!r}") from exc
    path = SCHEMA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"control-plane schema file missing: {path}")
    return path


def load_schema(name: str) -> dict[str, Any]:
    """Load and parse the schema registered under ``name``."""
    with schema_path(name).open("r", encoding="utf-8") as handle:
        return json.load(handle)
