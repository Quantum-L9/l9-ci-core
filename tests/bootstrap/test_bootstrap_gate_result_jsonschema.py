"""JSON Schema conformance tests for the bootstrap gate-result contract.

These tests validate concrete gate-result *documents* against the canonical
``schemas/bootstrap-gate-result.schema.json`` file (as opposed to the
``GateResult`` model invariants covered by ``test_bootstrap_result_schema.py``).
They lock the wire contract: a ``passed`` result may carry warnings but never
violations, and only known gate ids are accepted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schemas/bootstrap-gate-result.schema.json").read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)


def _validate(instance):
    errors = list(VALIDATOR.iter_errors(instance))
    assert not errors, [error.message for error in errors]


def test_passed_with_warning_is_valid():
    _validate({
        "schema_version": "1.0",
        "gate_id": "workflow/action-pins",
        "result": "passed",
        "violations": [],
        "warnings": [{
            "code": "MISSING_VERSION_ANNOTATION",
            "message": "Readable version annotation is absent.",
        }],
        "metadata": {},
    })


@pytest.mark.parametrize("gate_id", ["unknown", "", "action-pins"])
def test_unknown_gate_id_is_rejected(gate_id):
    instance = {
        "schema_version": "1.0",
        "gate_id": gate_id,
        "result": "passed",
        "violations": [],
        "warnings": [],
        "metadata": {},
    }
    assert list(VALIDATOR.iter_errors(instance))


def test_passed_with_violation_is_rejected():
    instance = {
        "schema_version": "1.0",
        "gate_id": "workflow/action-pins",
        "result": "passed",
        "violations": [{
            "code": "FLOATING_ACTION_REF",
            "message": "Floating reference.",
        }],
        "warnings": [],
        "metadata": {},
    }
    assert list(VALIDATOR.iter_errors(instance))
