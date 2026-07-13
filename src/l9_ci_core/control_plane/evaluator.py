"""Fail-closed promotion evaluator (stage skeleton).

Performs the expected-versus-received evaluation: verifies every canonical
result's schema, content hash and identity/policy binding; fails closed on any
missing, malformed, mismatched, skipped, cancelled, timed-out, unknown or
conflicting required result; enforces legacy required job outcomes; and emits a
structured promotion decision (``schemas/promotion-decision.schema.json``).
Control-plane corruption blocks regardless of gate mode.

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton.
"""

from __future__ import annotations

from typing import Any

__all__ = ["evaluate_promotion"]


def evaluate_promotion(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return a fail-closed promotion decision. Not yet implemented."""
    raise NotImplementedError(
        "evaluate_promotion is implemented in a later PR-B commit"
    )
