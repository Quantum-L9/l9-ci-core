"""Deterministic risk classification (stage skeleton).

Classifies a change into a risk tier from path selectors and labels: highest
matching tier wins, labels may raise but never lower path-derived risk, and an
unknown diff fails closed to ``high`` (``schemas/risk-tiers.schema.json``).

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton.
"""

from __future__ import annotations

from typing import Any

__all__ = ["classify_risk"]


def classify_risk(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return a risk classification. Not yet implemented."""
    raise NotImplementedError(
        "classify_risk is implemented in a later PR-B commit"
    )
