"""Safe gate executor.

Runs a selected gate's allowlisted local CLI command (``shell=False``, bounded
timeout, output constrained to an approved directory) and preserves the actual
PR-A base-result JSON. Never executes raw commands read from YAML.

Implemented in a later PR.
"""
from __future__ import annotations
