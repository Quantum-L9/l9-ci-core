"""Canonical JSON serialization.

The control plane hashes structured evidence and policy. To make those hashes
reproducible across machines, processes and library versions, every object is
serialized through one canonical form:

* keys sorted lexicographically
* compact separators (no incidental whitespace)
* UTF-8 encoding
* array order preserved (arrays are ordered data, never reordered)

Only the standard library is used so this module is importable without any
third-party dependency.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["dumps_canonical", "encode_canonical"]


def dumps_canonical(obj: Any) -> str:
    """Return the canonical JSON string for ``obj``.

    Uses sorted keys and compact separators. ``ensure_ascii`` is disabled so
    the output is genuine UTF-8 text rather than escaped ASCII; callers that
    need bytes should use :func:`encode_canonical`.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def encode_canonical(obj: Any) -> bytes:
    """Return the canonical JSON UTF-8 byte string for ``obj``."""
    return dumps_canonical(obj).encode("utf-8")
