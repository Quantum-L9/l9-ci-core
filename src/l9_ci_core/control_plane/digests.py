"""SHA-256 digest helpers.

Two digest flavours are used throughout the control plane:

* **source digest** -- ``sha256_bytes`` over the exact bytes of a file. Used
  for audit trails where the literal on-disk representation matters.
* **semantic digest** -- ``sha256_canonical`` over the canonical JSON encoding
  of parsed data. Two policy files that differ only in whitespace or key order
  share the same semantic digest; this is the identity used by evidence and by
  the evaluator.

All digests are returned in the prefixed form ``"sha256:<64 hex chars>"`` which
matches the digest pattern enforced by the JSON schemas.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .canonical_json import encode_canonical

__all__ = ["sha256_bytes", "sha256_canonical", "DIGEST_PREFIX"]

DIGEST_PREFIX = "sha256:"


def sha256_bytes(data: bytes) -> str:
    """Return the prefixed SHA-256 digest of raw ``data`` bytes."""
    return DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def sha256_canonical(obj: Any) -> str:
    """Return the prefixed SHA-256 digest of ``obj``'s canonical JSON form."""
    return sha256_bytes(encode_canonical(obj))
