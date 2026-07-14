"""Event-context normalization.

Normalizes a raw GitHub event payload (plus the ambient ``GITHUB_*`` values)
into a canonical :class:`~l9_ci_core.control_plane.models.EventContext`
(``schemas/event-context.schema.json``).

Subject SHA:  merge_group.head_sha -> pull_request.head.sha -> github.sha.
Base SHA:     merge_group.base_sha -> pull_request.base.sha -> push.before ->
              explicit workflow_dispatch input.

A missing/invalid subject SHA is fatal. A missing base SHA yields
``base_sha=None`` (the caller then treats the diff as unknown). The all-zero
SHA is accepted only as a push ``before`` (initial branch creation) and is
recorded as ``base_sha=None`` rather than used as a real base.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import SHA_RE, ZERO_SHA, EventContext, EventType

__all__ = ["ContextError", "normalize", "normalize_from_env"]


class ContextError(ValueError):
    """Raised when an event cannot be normalized into a valid context."""


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA_RE.match(value))


def _require_subject(value: Any, *, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContextError(f"missing subject SHA (expected from {source})")
    if value == ZERO_SHA:
        raise ContextError("subject SHA is the all-zero SHA (not a real commit)")
    if not _valid_sha(value):
        raise ContextError(f"invalid subject SHA {value!r} (from {source})")
    return value


def _optional_base(value: Any) -> str | None:
    """Return a valid base SHA, or None (missing / all-zero -> unknown diff)."""
    if not isinstance(value, str) or not value or value == ZERO_SHA:
        return None
    return value if _valid_sha(value) else None


def normalize(
    event: dict[str, Any],
    *,
    event_name: str,
    repository: str,
    github_sha: str | None = None,
    run_id: str = "",
    run_attempt: int = 1,
    actor: str = "",
    dispatch_subject_sha: str | None = None,
    dispatch_base_sha: str | None = None,
) -> EventContext:
    """Normalize a raw event payload into an :class:`EventContext`."""
    try:
        etype = EventType(event_name)
    except ValueError as exc:
        raise ContextError(f"unsupported event type: {event_name!r}") from exc

    if not repository or "/" not in repository:
        raise ContextError(f"invalid repository: {repository!r}")

    labels: list[str] = []
    labels_known = False
    pr_numbers: list[int] = []
    merge_group: dict[str, str | None] | None = None

    if etype is EventType.MERGE_GROUP:
        mg = event.get("merge_group") or {}
        subject = _require_subject(mg.get("head_sha"), source="merge_group.head_sha")
        base = _optional_base(mg.get("base_sha"))
        # Record the merge-group SHAs explicitly; risk is recomputed against
        # these, never reusing PR-head evidence. Labels are not reliable here.
        merge_group = {"head_sha": subject, "base_sha": base}
        labels_known = False
    elif etype is EventType.PULL_REQUEST:
        pr = event.get("pull_request") or {}
        subject = _require_subject(
            (pr.get("head") or {}).get("sha"), source="pull_request.head.sha"
        )
        base = _optional_base((pr.get("base") or {}).get("sha"))
        labels = [
            lbl["name"] for lbl in pr.get("labels", []) if isinstance(lbl, dict) and "name" in lbl
        ]
        labels_known = True
        if "number" in pr:
            pr_numbers = [int(pr["number"])]
    elif etype is EventType.PUSH:
        subject = _require_subject(github_sha or event.get("after"), source="github.sha")
        base = _optional_base(event.get("before"))
    else:  # workflow_dispatch
        subject = _require_subject(
            dispatch_subject_sha or github_sha, source="workflow_dispatch subject"
        )
        base = _optional_base(dispatch_base_sha)

    return EventContext(
        repository=repository,
        event_type=etype,
        subject_sha=subject,
        base_sha=base,
        pull_request_numbers=pr_numbers,
        merge_group=merge_group,
        labels=labels,
        labels_known=labels_known,
        actor=actor,
        run_id=str(run_id),
        run_attempt=int(run_attempt),
    )


def _repo_from_event(event: dict[str, Any]) -> str:
    repo = event.get("repository")
    if isinstance(repo, dict):
        return repo.get("full_name", "")
    if isinstance(repo, str):
        return repo
    return ""


def normalize_from_env(
    event_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    dispatch_subject_sha: str | None = None,
    dispatch_base_sha: str | None = None,
) -> EventContext:
    """Normalize using a GitHub event file plus the ambient ``GITHUB_*`` env.

    The ``GITHUB_*`` environment wins (that is how GitHub Actions supplies the
    context). When it is absent — e.g. a local run against a fixture — the event
    file's own ``event_name`` / ``repository`` / ``sha`` / ``run_*`` / ``actor``
    fields are used as fallbacks so fixtures are self-describing.
    """
    env = dict(os.environ if env is None else env)
    text = Path(event_path).read_text(encoding="utf-8") if event_path else "{}"
    event = json.loads(text or "{}")
    return normalize(
        event,
        event_name=env.get("GITHUB_EVENT_NAME") or event.get("event_name", ""),
        repository=env.get("GITHUB_REPOSITORY") or _repo_from_event(event),
        github_sha=env.get("GITHUB_SHA") or event.get("after") or event.get("sha"),
        run_id=env.get("GITHUB_RUN_ID") or str(event.get("run_id", "")),
        run_attempt=int(env.get("GITHUB_RUN_ATTEMPT") or event.get("run_attempt", 1) or 1),
        actor=env.get("GITHUB_ACTOR") or event.get("actor", ""),
        dispatch_subject_sha=dispatch_subject_sha,
        dispatch_base_sha=dispatch_base_sha,
    )
