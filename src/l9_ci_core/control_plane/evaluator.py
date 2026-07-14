"""Fail-closed promotion evaluator.

Compares expected (planned) gates against received canonical evidence, verifies
every content hash and policy/subject identity, enforces legacy job outcomes,
and emits a fail-closed promotion decision (see
``schemas/promotion-decision.schema.json``). Control-plane corruption blocks
regardless of gate mode.

Implemented in a later PR.
"""

from __future__ import annotations
