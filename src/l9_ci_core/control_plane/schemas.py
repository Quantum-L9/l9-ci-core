"""Locate, load, and validate against the repository JSON Schemas.

Schemas live in the repo-root ``schemas/`` directory (they are shared with the
PR-A bootstrap tooling and are not packaged inside ``l9_ci_core``). This module
locates that directory without any ``sys.path`` manipulation:

1. the ``L9_SCHEMAS_DIR`` environment variable, if set;
2. walking up from this file (the editable install lives inside the repo);
3. walking up from the current working directory.

All control-plane schemas are JSON Schema Draft 2020-12. A missing schema file
is always fatal to the caller — the control plane never silently skips
validation because a schema is absent.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

__all__ = [
    "SchemaNotFound",
    "schemas_dir",
    "load_schema",
    "validator_for",
    "iter_errors",
    "validate",
    "format_error",
]

# A schema known to exist in every valid schemas/ directory (shipped by PR-A),
# used as the sentinel when auto-discovering the directory.
_SENTINEL = "bootstrap-gate-result.schema.json"


class SchemaNotFound(FileNotFoundError):
    """Raised when a required schema file cannot be located."""


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("L9_SCHEMAS_DIR", "").strip()
    if env:
        roots.append(Path(env))
    here = Path(__file__).resolve()
    for parent in here.parents:
        roots.append(parent / "schemas")
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        roots.append(parent / "schemas")
    # De-duplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


@lru_cache(maxsize=1)
def schemas_dir() -> Path:
    """Return the repository ``schemas/`` directory.

    Raises :class:`SchemaNotFound` if it cannot be located.
    """
    for root in _candidate_roots():
        if (root / _SENTINEL).is_file():
            return root
    raise SchemaNotFound(
        "Could not locate a schemas/ directory containing "
        f"{_SENTINEL!r}; set L9_SCHEMAS_DIR to override."
    )


def load_schema(name: str) -> dict[str, Any]:
    """Load and structurally check the named schema (e.g. ``gate-plan``).

    ``name`` may omit the ``.schema.json`` suffix.
    """
    filename = name if name.endswith(".json") else f"{name}.schema.json"
    path = schemas_dir() / filename
    if not path.is_file():
        raise SchemaNotFound(f"Required schema is missing: {path}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=None)
def validator_for(name: str) -> Draft202012Validator:
    """Return a cached Draft 2020-12 validator for the named schema."""
    return Draft202012Validator(load_schema(name), format_checker=FormatChecker())


def iter_errors(name: str, instance: Any) -> list:
    """Return schema-validation errors for ``instance``, sorted by path."""
    validator = validator_for(name)
    return sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))


def format_error(error) -> str:
    """Human-readable ``path: message`` for a validation error."""
    path = ".".join(str(part) for part in error.absolute_path)
    return f"{path}: {error.message}" if path else error.message


def validate(name: str, instance: Any) -> None:
    """Validate ``instance`` against the named schema, raising on any error."""
    errors = iter_errors(name, instance)
    if errors:
        joined = "; ".join(format_error(e) for e in errors)
        raise ValueError(f"{name} schema validation failed: {joined}")
