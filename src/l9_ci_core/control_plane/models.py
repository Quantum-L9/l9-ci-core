"""Typed control-plane vocabulary.

Central definition of the closed value sets that the registry loader, planner
and evaluator validate against. Keeping them here (stdlib only) means every
stage agrees on the same spelling of every owner layer, mode, risk tier, gate
identity and control-plane error code.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "SCHEMA_VERSION",
    "OwnerLayer",
    "GateMode",
    "RiskTier",
    "LifecycleStatus",
    "ExecutorType",
    "ResultAdapter",
    "GateOutcome",
    "Decision",
    "MergeMeaning",
    "BOOTSTRAP_GATE_IDS",
    "LEGACY_REQUIREMENTS",
    "RISK_TIER_RANKS",
]

# The only schema version this control-plane generation understands. Anything
# else is rejected as an unsupported version.
SCHEMA_VERSION = "1.0"


class OwnerLayer(str, Enum):
    """Layer accountable for a gate's policy."""

    POLICY_RUNTIME = "l9_policy_runtime"
    ASSURANCE = "l9_assurance"


class GateMode(str, Enum):
    """Enforcement mode of a gate."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"
    SHADOW = "shadow"
    DISABLED = "disabled"


class RiskTier(str, Enum):
    """Deterministic risk classification of a change."""

    LOW = "low"
    STANDARD = "standard"
    HIGH = "high"
    REGULATED = "regulated"


class LifecycleStatus(str, Enum):
    """Lifecycle state of a registered gate."""

    ACTIVE = "active"
    WARN = "warn"
    RETIRED = "retired"


class ExecutorType(str, Enum):
    """Supported gate executor adapters."""

    LOCAL_CLI = "local_cli"


class ResultAdapter(str, Enum):
    """Supported base-result adapters."""

    BOOTSTRAP_V1 = "bootstrap_v1"


class GateOutcome(str, Enum):
    """Outcome values a validator may actually produce.

    These are the *real* tool outcomes only. Synthetic states (missing,
    skipped, cancelled, timed_out, ...) live in the evaluator/promotion
    decision and are never written into a base result.
    """

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class Decision(str, Enum):
    """Final promotion decision."""

    PASSED = "passed"
    BLOCKED = "blocked"


class MergeMeaning(str, Enum):
    """Human-meaningful reason behind a promotion decision."""

    ALL_REQUIRED_GATES_PASSED = "all_required_gates_passed"
    REQUIRED_GATE_FAILED = "required_gate_failed"
    LEGACY_REQUIRED_JOB_FAILED = "legacy_required_job_failed"
    CONTROL_PLANE_FAILURE = "control_plane_failure"


# The four PR-A bootstrap gates registered by PR-B. Order is not significant;
# planning output is always sorted by gate id.
BOOTSTRAP_GATE_IDS = (
    "workflow/action-pins",
    "workflow/download-integrity",
    "dependencies/ci-lock",
    "workflow/contracts",
)

# Legacy required jobs preserved during the PR-B transition. They are enforced
# by the evaluator and only removed once PR-C migrates each into a gate.
LEGACY_REQUIREMENTS = (
    "validate",
    "lint",
    "semgrep",
    "test",
    "security",
)

# Numeric ranks for risk precedence; highest rank wins.
RISK_TIER_RANKS = {
    RiskTier.LOW: 10,
    RiskTier.STANDARD: 20,
    RiskTier.HIGH: 30,
    RiskTier.REGULATED: 40,
}
