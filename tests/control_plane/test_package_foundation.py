"""PR-B1: package installs and imports cleanly; schemas + primitives are sound.

These tests import the *installed* package normally — no sys.path insertion,
no PYTHONPATH. They cover the B1 deliverables: the package skeleton, the nine
control-plane schemas, and the canonical-json / digest / schema-loader
foundations that PR-B2 builds on.
"""

from __future__ import annotations

import importlib

import pytest

CONTROL_PLANE_MODULES = [
    "l9_ci_core",
    "l9_ci_core.control_plane",
    "l9_ci_core.control_plane.canonical_json",
    "l9_ci_core.control_plane.changed_files",
    "l9_ci_core.control_plane.context",
    "l9_ci_core.control_plane.digests",
    "l9_ci_core.control_plane.evaluator",
    "l9_ci_core.control_plane.executor",
    "l9_ci_core.control_plane.models",
    "l9_ci_core.control_plane.planner",
    "l9_ci_core.control_plane.registry",
    "l9_ci_core.control_plane.results",
    "l9_ci_core.control_plane.risk",
    "l9_ci_core.control_plane.schemas",
]

NINE_SCHEMAS = [
    "event-context",
    "changed-files",
    "gate-registry",
    "risk-tiers",
    "gate-plan",
    "gate-result",
    "legacy-job-results",
    "promotion-decision",
    "control-plane-migration",
]


@pytest.mark.parametrize("module", CONTROL_PLANE_MODULES)
def test_module_imports(module):
    assert importlib.import_module(module) is not None


def test_top_level_import_contract():
    # The exact command the B1 contract requires to pass in a clean env.
    import l9_ci_core.control_plane as cp

    assert cp.SCHEMA_VERSION == "1.0"


@pytest.mark.parametrize("name", NINE_SCHEMAS)
def test_schema_loads_and_is_draft202012(name):
    from jsonschema import Draft202012Validator

    from l9_ci_core.control_plane import schemas

    schema = schemas.load_schema(name)
    # load_schema already calls check_schema; assert the declared dialect too.
    assert schema["$schema"].endswith("draft/2020-12/schema")
    assert schema.get("additionalProperties") is False
    Draft202012Validator.check_schema(schema)


def test_missing_schema_is_fatal():
    from l9_ci_core.control_plane import schemas

    with pytest.raises(schemas.SchemaNotFound):
        schemas.load_schema("this-schema-does-not-exist")


def test_canonical_json_is_deterministic_and_key_order_independent():
    from l9_ci_core.control_plane.canonical_json import canonical_dumps

    a = {"b": 1, "a": [3, 2, 1], "c": {"y": 1, "x": 2}}
    b = {"c": {"x": 2, "y": 1}, "a": [3, 2, 1], "b": 1}
    assert canonical_dumps(a) == canonical_dumps(b)
    # Array order is preserved (arrays are ordered data).
    assert canonical_dumps([3, 2, 1]) != canonical_dumps([1, 2, 3])


def test_content_hash_excludes_named_field():
    from l9_ci_core.control_plane.canonical_json import content_hash

    base = {"x": 1, "y": 2}
    with_hash = {"x": 1, "y": 2, "content_hash": "sha256:deadbeef"}
    # Hashing with the hash field excluded reproduces the pre-hash value.
    assert content_hash(with_hash, exclude="content_hash") == content_hash(base)
    assert content_hash(base).startswith("sha256:")


def test_semantic_digest_stable_across_whitespace_only_reflow(tmp_path):
    from l9_ci_core.control_plane import digests

    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("schema_version: '1.0'\nvalues:\n  - one\n  - two\n")
    b.write_text("schema_version: '1.0'\n\nvalues: [one, two]\n")

    src_a, sem_a, _ = digests.policy_digests(a)
    src_b, sem_b, _ = digests.policy_digests(b)

    # Different bytes -> different source digests, identical semantics.
    assert src_a != src_b
    assert sem_a == sem_b
    assert sem_a.startswith("sha256:")


def test_event_context_roundtrip_and_shape():
    from l9_ci_core.control_plane import schemas
    from l9_ci_core.control_plane.models import EventContext, EventType

    ctx = EventContext(
        repository="Quantum-L9/l9-ci-core",
        event_type=EventType.PULL_REQUEST,
        subject_sha="a" * 40,
        base_sha="b" * 40,
        pull_request_numbers=[1],
        labels=["risk:high"],
        labels_known=True,
        actor="octocat",
        run_id="123",
        run_attempt=1,
    )
    data = ctx.to_dict()
    schemas.validate("event-context", data)
    assert EventContext.from_dict(data) == ctx
