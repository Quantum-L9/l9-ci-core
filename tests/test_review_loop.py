from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def test_review_routing_policy_is_advisory_first() -> None:
    policy = yaml.safe_load((ROOT / ".github/governance/review-routing-policy.yaml").read_text())
    assert policy["default"]["mode"] == "advisory"
    assert policy["routing"]["security"]["fail_closed"] is True
    assert "llm" in policy["routing"]["security"]["agents"]
    assert policy["routing"]["docs_only"]["agents"] == ["audit"]
    assert set(policy["model_tiers"]) == {"fast", "mid", "strong", "strongest"}


def test_review_report_schema_is_valid_and_pins_marker() -> None:
    schema = json.loads((ROOT / "schemas/agent-review-report.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["marker"]["const"] == "<!-- l9-agent-review-marker: v1 -->"


def test_code_review_workflow_is_reusable_and_advisory() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/code-review.yml").read_text())
    on = wf.get(True, wf.get("on"))
    assert "workflow_call" in on
    job = wf["jobs"]["agent-review"]
    assert job["permissions"]["pull-requests"] == "write"
    text = (ROOT / ".github/workflows/code-review.yml").read_text()
    # Advisory: the review step must not use --strict (no merge authority).
    assert "--strict" not in text
    assert "l9-agent-review-marker: v1" in text
