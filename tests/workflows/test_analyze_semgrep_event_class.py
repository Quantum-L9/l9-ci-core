"""Contract checks for reusable analyze-semgrep governance event classes."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "analyze-semgrep.yml"


def _workflow() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if True in document:
        document["on"] = document.pop(True)
    assert isinstance(document, dict)
    return document


def _governance_event_expression(document: dict) -> str:
    for step in document["jobs"]["analyze"]["steps"]:
        if step.get("id") == "gov":
            return str(step["with"]["event-name"])
    raise AssertionError("analyze-semgrep.yml has no governance resolver step")


def _semgrep_run_step(document: dict) -> dict:
    for step in document["jobs"]["analyze"]["steps"]:
        if step.get("with", {}).get("operation") == "semgrep-run":
            return step
    raise AssertionError("analyze-semgrep.yml has no SDK Semgrep run step")


def test_reusable_kernel_accepts_an_explicit_governance_event_class() -> None:
    document = _workflow()
    event_input = document["on"]["workflow_call"]["inputs"]["event"]
    assert event_input["required"] is False
    assert event_input["default"] == ""

    expression = _governance_event_expression(document)
    assert "inputs.event != ''" in expression
    assert "inputs.event" in expression


def test_manual_dispatch_without_an_override_stays_a_nightly_canary() -> None:
    expression = _governance_event_expression(_workflow())
    assert "github.event_name == 'workflow_dispatch'" in expression
    assert "'nightly'" in expression


def test_explicit_event_precedes_the_manual_nightly_fallback() -> None:
    expression = " ".join(_governance_event_expression(_workflow()).split())
    explicit = expression.index("inputs.event != ''")
    fallback = expression.index("github.event_name == 'workflow_dispatch'")
    assert explicit < fallback


def test_semgrep_run_uses_the_core_staged_map_for_its_bounded_language() -> None:
    step = _semgrep_run_step(_workflow())
    assert step["with"]["identity-map"] == (
        "${{ steps.gov.outputs['identity-map-directory'] }}/${{ inputs.language }}.yaml"
    )
