"""PR-B1 canonical schema well-formedness tests.

Deliberately stdlib-only (``json`` + the schema registry), so they run under a
``--no-deps`` install. They assert the nine canonical schemas exist, parse, are
declared against Draft 2020-12, pin ``schema_version`` to reject unsupported
versions, and lock down controlled objects with ``additionalProperties: false``.
Full JSON-Schema-driven validation of instances lands with the stages that
consume these schemas.
"""

from __future__ import annotations

import json

import pytest

from l9_ci_core.control_plane import schemas

EXPECTED_SCHEMAS = {
    "event-context",
    "changed-files",
    "gate-registry",
    "risk-tiers",
    "gate-plan",
    "gate-result",
    "legacy-job-results",
    "promotion-decision",
    "control-plane-migration",
}

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def test_registry_lists_exactly_nine_schemas():
    assert set(schemas.SCHEMAS) == EXPECTED_SCHEMAS
    assert len(schemas.SCHEMAS) == 9


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_schema_file_exists_and_parses(name):
    schema = schemas.load_schema(name)
    assert isinstance(schema, dict)


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_schema_declares_draft_2020_12(name):
    assert schemas.load_schema(name)["$schema"] == DRAFT_2020_12


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_top_level_object_forbids_additional_properties(name):
    schema = schemas.load_schema(name)
    assert schema.get("type") == "object"
    assert schema.get("additionalProperties") is False


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_schema_version_is_pinned(name):
    schema = schemas.load_schema(name)
    version = schema["properties"]["schema_version"]
    # Pinned to a single supported version so unsupported versions are rejected.
    assert version == {"const": "1.0"}


def test_sha_and_digest_patterns_present_where_expected():
    plan = schemas.load_schema("gate-plan")
    assert plan["properties"]["subject_sha"]["pattern"] == "^[0-9a-f]{40}$"
    assert plan["$defs"]["digest"]["pattern"] == "^sha256:[0-9a-f]{64}$"


def test_missing_schema_name_raises():
    with pytest.raises(KeyError):
        schemas.schema_path("does-not-exist")
