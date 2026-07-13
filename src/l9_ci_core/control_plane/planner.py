"""Deterministic gate planner.

Selects gates from the registry for a given event, changed-file set, and risk
tier, and emits a stable, digest-bound plan (see ``schemas/gate-plan.schema.json``).

Implemented in a later PR.
"""
from __future__ import annotations
