"""Fail-closed governance schema loading for the bootstrap CI gates.

Authority order enforced here: security contract -> production invariant ->
implementation -> tests. Every governance schema shipped with this pack is a
*required* dependency of the CLI gate path. Its absence (or the absence of the
``jsonschema`` library, which is pinned in ``requirements/bootstrap.lock``) means
evidence cannot be trusted, so validation MUST fail closed rather than silently
downgrade to weaker structural checks.

The optional/graceful-degradation behavior is intentionally *not* provided here.
If a third party wants to embed these checks without schemas, they can build a
validator object directly; the CI gate CLI must not.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
    _HAVE_JSONSCHEMA = True
except Exception:  # pragma: no cover - jsonschema is a locked dependency
    Draft202012Validator = None  # type: ignore
    FormatChecker = None  # type: ignore
    _HAVE_JSONSCHEMA = False


class SchemaUnavailable(RuntimeError):
    """Raised when a required schema or the jsonschema library is missing.

    The CLI layer converts this into a gate ``error`` result with exit code 2.
    """


def have_jsonschema() -> bool:
    return _HAVE_JSONSCHEMA


def load_validator(root: Path, schema_name: str):
    """Return a Draft 2020-12 validator for ``schemas/<schema_name>``.

    Fails closed: raises :class:`SchemaUnavailable` when ``jsonschema`` is not
    importable or the schema file is absent/unreadable. Never returns ``None``.
    """
    if not _HAVE_JSONSCHEMA:
        raise SchemaUnavailable(
            "jsonschema dependency is unavailable; it is a required "
            "dependency of the bootstrap CI gates (requirements/bootstrap.lock)."
        )
    # Callers may pass either the bare stem (``ci-dependency-exceptions``) or
    # the full filename (``ci-dependency-exceptions.schema.json``).
    filename = schema_name if schema_name.endswith(".schema.json") else f"{schema_name}.schema.json"
    schema_path = Path(root) / "schemas" / filename
    if not schema_path.is_file():
        raise SchemaUnavailable(f"Required schema is missing: {schema_path}")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:  # malformed schema is also fatal
        raise SchemaUnavailable(f"Required schema is unreadable: {schema_path}: {exc}")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def schema_errors(validator, data) -> list:
    """Sorted validation errors for ``data`` under ``validator`` (never None)."""
    return sorted(
        validator.iter_errors(data),
        key=lambda item: list(item.absolute_path),
    )


def format_error(error) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return f"{path}: {error.message}" if path else error.message
