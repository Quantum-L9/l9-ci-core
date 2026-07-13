"""Gate-registry loader and semantic validation.

Loads ``.github/governance/gate-registry.yaml``, validates it against
``schemas/gate-registry.schema.json`` and the additional semantic rules
(unknown owner layers/modes/tiers, missing schemas/commands, nonpositive
timeouts, blocking-without-evidence, etc.), and exposes typed gate specs.

Implemented in PR-B2.
"""
from __future__ import annotations
