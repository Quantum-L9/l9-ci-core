"""Typed domain vocabulary for the control plane.

This module holds the shared enums, format constants, and the data-transfer
objects that cross module boundaries (event context, changed files). Loaders
for the gate registry and risk policy build on these types in PR-B2.

The string values of every enum are the *wire* values used in JSON/YAML and in
the schemas; they must never drift from the schema ``enum`` lists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "1.0"

# 40-char lowercase git SHA; the all-zero SHA is modeled explicitly elsewhere.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ZERO_SHA = "0" * 40
# "sha256:" + 64 lowercase hex.
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class EventType(StrEnum):
    PULL_REQUEST = "pull_request"
    MERGE_GROUP = "merge_group"
    PUSH = "push"
    WORKFLOW_DISPATCH = "workflow_dispatch"


class RiskTier(StrEnum):
    LOW = "low"
    STANDARD = "standard"
    HIGH = "high"
    REGULATED = "regulated"


class GateMode(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"
    SHADOW = "shadow"
    DISABLED = "disabled"


class OwnerLayer(StrEnum):
    L9_POLICY_RUNTIME = "l9_policy_runtime"
    L9_ASSURANCE = "l9_assurance"
    L9_CI_CORE = "l9_ci_core"
    L9_PLATFORM = "l9_platform"


class ExecutorType(StrEnum):
    LOCAL_CLI = "local_cli"


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    WARN = "warn"
    RETIRED = "retired"


class ResultStatus(StrEnum):
    """Actual tool-produced outcomes (PR-A base results and canonical results)."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class Decision(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


class MergeMeaning(StrEnum):
    ALL_REQUIRED_GATES_PASSED = "all_required_gates_passed"
    REQUIRED_GATE_FAILED = "required_gate_failed"
    LEGACY_REQUIRED_JOB_FAILED = "legacy_required_job_failed"
    CONTROL_PLANE_FAILURE = "control_plane_failure"


def is_sha(value: Any) -> bool:
    """True if ``value`` is a 40-char lowercase hex SHA."""
    return isinstance(value, str) and bool(SHA_RE.match(value))


def is_digest(value: Any) -> bool:
    """True if ``value`` is a ``sha256:<64hex>`` digest string."""
    return isinstance(value, str) and bool(DIGEST_RE.match(value))


@dataclass(frozen=True, slots=True)
class EventContext:
    """Normalized view of a GitHub event (see ``event-context.schema.json``)."""

    repository: str
    event_type: EventType
    subject_sha: str
    base_sha: str | None
    pull_request_numbers: list[int] = field(default_factory=list)
    merge_group: dict[str, Any] | None = None
    labels: list[str] = field(default_factory=list)
    labels_known: bool = False
    actor: str = ""
    run_id: str = ""
    run_attempt: int = 1
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repository": self.repository,
            "event_type": str(self.event_type),
            "subject_sha": self.subject_sha,
            "base_sha": self.base_sha,
            "pull_request_numbers": list(self.pull_request_numbers),
            "merge_group": self.merge_group,
            "labels": list(self.labels),
            "labels_known": self.labels_known,
            "actor": self.actor,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventContext":
        return cls(
            repository=data["repository"],
            event_type=EventType(data["event_type"]),
            subject_sha=data["subject_sha"],
            base_sha=data.get("base_sha"),
            pull_request_numbers=list(data.get("pull_request_numbers", [])),
            merge_group=data.get("merge_group"),
            labels=list(data.get("labels", [])),
            labels_known=bool(data.get("labels_known", False)),
            actor=data.get("actor", ""),
            run_id=str(data.get("run_id", "")),
            run_attempt=int(data.get("run_attempt", 1)),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class ChangedFiles:
    """Result of changed-file acquisition (see ``changed-files.schema.json``)."""

    files: list[str] = field(default_factory=list)
    unknown_diff: bool = False
    reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "files": list(self.files),
            "unknown_diff": self.unknown_diff,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChangedFiles":
        return cls(
            files=list(data.get("files", [])),
            unknown_diff=bool(data.get("unknown_diff", False)),
            reason=data.get("reason"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
