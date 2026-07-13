"""PR-B2: event-context normalization (PR §7)."""
from __future__ import annotations

import json

import pytest

from l9_ci_core.control_plane import context, schemas
from l9_ci_core.control_plane.models import EventType

REPO = "Quantum-L9/l9-ci-core"
SHA_A = "1" * 40
SHA_B = "2" * 40
ZERO = "0" * 40


def _norm(event, **kw):
    kw.setdefault("repository", REPO)
    return context.normalize(event, **kw)


def _valid(ctx):
    schemas.validate("event-context", ctx.to_dict())
    return ctx


def test_pull_request_uses_head_and_base_and_captures_labels():
    ctx = _norm(
        {
            "pull_request": {
                "number": 7,
                "head": {"sha": SHA_A},
                "base": {"sha": SHA_B},
                "labels": [{"name": "risk:high"}, {"name": "area:ci"}],
            }
        },
        event_name="pull_request",
    )
    _valid(ctx)
    assert ctx.event_type is EventType.PULL_REQUEST
    assert ctx.subject_sha == SHA_A
    assert ctx.base_sha == SHA_B
    assert ctx.pull_request_numbers == [7]
    assert ctx.labels == ["risk:high", "area:ci"]
    assert ctx.labels_known is True


def test_merge_group_uses_merge_group_shas_and_labels_unknown():
    ctx = _norm(
        {"merge_group": {"head_sha": SHA_A, "base_sha": SHA_B}},
        event_name="merge_group",
    )
    _valid(ctx)
    assert ctx.subject_sha == SHA_A
    assert ctx.base_sha == SHA_B
    assert ctx.merge_group == {"head_sha": SHA_A, "base_sha": SHA_B}
    assert ctx.labels_known is False


def test_push_uses_github_sha_and_before():
    ctx = _norm(
        {"before": SHA_B, "after": SHA_A},
        event_name="push",
        github_sha=SHA_A,
    )
    _valid(ctx)
    assert ctx.subject_sha == SHA_A
    assert ctx.base_sha == SHA_B
    assert ctx.labels_known is False


def test_workflow_dispatch_with_explicit_shas():
    ctx = _norm(
        {},
        event_name="workflow_dispatch",
        github_sha=SHA_A,
        dispatch_subject_sha=SHA_A,
        dispatch_base_sha=SHA_B,
    )
    _valid(ctx)
    assert ctx.subject_sha == SHA_A
    assert ctx.base_sha == SHA_B


def test_workflow_dispatch_missing_base_yields_unknown_base():
    ctx = _norm({}, event_name="workflow_dispatch", github_sha=SHA_A)
    _valid(ctx)
    assert ctx.subject_sha == SHA_A
    assert ctx.base_sha is None


def test_invalid_subject_sha_is_fatal():
    with pytest.raises(context.ContextError):
        _norm(
            {"pull_request": {"head": {"sha": "not-a-sha"}, "base": {"sha": SHA_B}}},
            event_name="pull_request",
        )


def test_missing_subject_sha_is_fatal():
    with pytest.raises(context.ContextError):
        _norm({"pull_request": {"base": {"sha": SHA_B}}}, event_name="pull_request")


def test_missing_base_sha_triggers_unknown_base():
    ctx = _norm(
        {"pull_request": {"head": {"sha": SHA_A}}},
        event_name="pull_request",
    )
    _valid(ctx)
    assert ctx.base_sha is None


def test_initial_branch_creation_zero_before_is_unknown_base():
    ctx = _norm({"before": ZERO, "after": SHA_A}, event_name="push", github_sha=SHA_A)
    _valid(ctx)
    assert ctx.subject_sha == SHA_A
    # All-zero before (branch creation) is recorded as unknown, not used as base.
    assert ctx.base_sha is None


def test_all_zero_subject_is_rejected():
    with pytest.raises(context.ContextError):
        _norm({"before": SHA_B, "after": ZERO}, event_name="push", github_sha=ZERO)


def test_unsupported_event_type_is_fatal():
    with pytest.raises(context.ContextError):
        _norm({}, event_name="schedule", github_sha=SHA_A)


def test_from_env_reads_self_describing_fixture(tmp_path):
    fixture = tmp_path / "event.json"
    fixture.write_text(
        json.dumps(
            {
                "event_name": "pull_request",
                "repository": {"full_name": REPO},
                "pull_request": {
                    "number": 1,
                    "head": {"sha": SHA_A},
                    "base": {"sha": SHA_B},
                    "labels": [],
                },
            }
        )
    )
    ctx = context.normalize_from_env(fixture, env={})
    _valid(ctx)
    assert ctx.repository == REPO
    assert ctx.subject_sha == SHA_A


def test_malformed_event_file_is_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        context.normalize_from_env(bad, env={})
