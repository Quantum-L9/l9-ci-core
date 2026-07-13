"""Event-context normalization (stage skeleton).

Normalizes a raw GitHub event into the canonical event-context document
(``schemas/event-context.schema.json``): subject/base SHAs, event type,
labels, merge-group object and run identity.

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton; the signature below fixes the public entrypoint so
callers and the CLI wrapper can bind to it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["normalize_event_context"]


def normalize_event_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return a normalized event-context document. Not yet implemented."""
    raise NotImplementedError(
        "normalize_event_context is implemented in a later PR-B commit"
    )
