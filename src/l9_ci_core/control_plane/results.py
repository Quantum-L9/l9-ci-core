"""Bootstrap-to-canonical result enrichment (stage skeleton).

Wraps an actual PR-A base result in the canonical gate-result envelope: binds
identity, subject and policy digests, preserves the base result byte-for-byte,
and computes the content hash over the canonical object excluding the hash
field (``schemas/gate-result.schema.json``).

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton.
"""

from __future__ import annotations

from typing import Any

__all__ = ["enrich_gate_result"]


def enrich_gate_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return a canonical, content-hashed gate result. Not yet implemented."""
    raise NotImplementedError(
        "enrich_gate_result is implemented in a later PR-B commit"
    )
