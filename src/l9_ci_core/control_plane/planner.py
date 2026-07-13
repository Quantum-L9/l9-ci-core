"""Deterministic gate planner (stage skeleton).

Selects gates from the validated registry given event context, changed files,
risk tier and rule modes; records a reason for every selected and not-selected
gate; computes semantic policy digests and a plan digest over canonical,
timestamp-free plan data (``schemas/gate-plan.schema.json``).

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton. The public :func:`plan_gates` symbol is defined here so
that ``from l9_ci_core.control_plane.planner import plan_gates`` binds.
"""

from __future__ import annotations

from typing import Any

__all__ = ["plan_gates"]


def plan_gates(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return a deterministic gate plan. Not yet implemented."""
    raise NotImplementedError(
        "plan_gates is implemented in a later PR-B commit"
    )
