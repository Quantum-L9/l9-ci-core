"""Gate registry loading and semantic validation (stage skeleton).

Loads ``.github/governance/gate-registry.yaml``, validates it against
``schemas/gate-registry.schema.json`` and enforces the semantic rules (no
duplicate ids, known owner layers/modes/tiers, allowlisted command keys,
referenced schemas present, positive timeouts, ...). A missing registry schema
is fatal.

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton; YAML/jsonschema are imported lazily inside functions so
importing this module needs no third-party dependency.
"""

from __future__ import annotations

from typing import Any

__all__ = ["load_registry"]


def load_registry(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Load and validate the gate registry. Not yet implemented."""
    raise NotImplementedError(
        "load_registry is implemented in a later PR-B commit"
    )
