"""Canonical result enrichment.

Wraps an actual PR-A base result in canonical evidence bound to subject and
policy identity, preserving the base result byte-for-byte and attaching a
verified content hash (see ``schemas/gate-result.schema.json``).

Implemented in a later PR.
"""
from __future__ import annotations
