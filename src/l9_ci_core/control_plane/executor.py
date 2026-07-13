"""Safe gate executor (stage skeleton).

Runs a selected gate through a hardcoded, allowlisted command table
(``shell=False``, registry-derived bounded timeout, constrained output
directory), asserts the validator exit code agrees with the base-result
outcome, and preserves the actual base-result JSON. A crash with no result
yields a structured execution-failure envelope, never a fake base result.

Full behavior is implemented in a later PR-B commit. This module is part of the
PR-B1 package skeleton.
"""

from __future__ import annotations

from typing import Any

__all__ = ["COMMANDS", "execute_registered_gate"]

# Allowlisted commands, keyed by the registry's ``command_key``. The YAML
# registry references only these keys; raw commands are never read from YAML
# and never reach a shell.
COMMANDS: dict[str, list[str]] = {
    "validate_action_pins": [
        "python",
        ".github/scripts/validate_action_pins.py",
    ],
    "validate_download_integrity": [
        "python",
        ".github/scripts/validate_download_integrity.py",
    ],
    "validate_ci_dependencies": [
        "python",
        ".github/scripts/validate_ci_dependencies.py",
    ],
    "validate_workflow_contracts": [
        "python",
        ".github/scripts/validate_workflow_contracts.py",
    ],
}


def execute_registered_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Execute an allowlisted gate command. Not yet implemented."""
    raise NotImplementedError(
        "execute_registered_gate is implemented in a later PR-B commit"
    )
