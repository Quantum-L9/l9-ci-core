"""Deterministic (canonical) JSON encoding and content hashing.

Canonical form, used for every digest and content hash in the control plane:

* object keys sorted lexicographically,
* compact separators (no incidental whitespace),
* UTF-8, non-ASCII preserved (``ensure_ascii=False``),
* array order preserved (arrays are ordered data, never sorted),
* no trailing newline.

Determinism is a hard requirement: the same logical value must always produce
the same bytes, on any platform, so hashes are comparable across runs and
machines. There are therefore no timestamps, randomness, or environment
inputs anywhere in this module.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "canonical_dumps",
    "canonical_bytes",
    "sha256_hex",
    "content_hash",
]


def canonical_dumps(value: Any) -> str:
    """Return the canonical JSON text for ``value``."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical JSON encoding of ``value`` as UTF-8 bytes."""
    return canonical_dumps(value).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    """Return the hex sha256 of ``data`` (str is encoded as UTF-8)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any, *, exclude: str | None = None) -> str:
    """Return ``"sha256:<hex>"`` over the canonical encoding of ``value``.

    When ``exclude`` is given and ``value`` is a mapping, that top-level key is
    dropped before hashing. This is how a self-describing object (a gate result
    or a plan) hashes everything *except* the field that will hold the hash.
    """
    if exclude is not None and isinstance(value, dict):
        value = {k: v for k, v in value.items() if k != exclude}
    return "sha256:" + sha256_hex(canonical_bytes(value))
