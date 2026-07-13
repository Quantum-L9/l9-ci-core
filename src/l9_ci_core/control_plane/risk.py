"""Deterministic risk classification.

Classifies a changed-file set (plus labels) into a risk tier using
``.github/governance/risk-tiers.yaml`` (see ``schemas/risk-tiers.schema.json``).
Highest matching tier wins; labels may raise but never lower path-derived risk;
an unknown diff yields ``high``.

Implemented in PR-B2.
"""
from __future__ import annotations
