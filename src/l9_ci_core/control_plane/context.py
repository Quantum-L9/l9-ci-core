"""Event-context normalization.

Normalizes a raw GitHub event payload into a canonical
:class:`~l9_ci_core.control_plane.models.EventContext`
(see ``schemas/event-context.schema.json``).

Implemented in PR-B2.
"""
from __future__ import annotations
