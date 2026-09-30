"""Contract checks for reusable analyze-semgrep governance event classes."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "analyze-semgrep.yml"
GOVERNANCE_RESOLVER_PIN = "59cb364615ccd44d824bec6b71d49d2f93e465c9"


def _workflow() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if True in document:
        document["on"] = document.pop(True)
    assert isinstance(document, dict)
    return document


def _semgrep_run_step(document: dict) -> dict:
    for step in document["jobs"]["analyze"]["steps"]:
        if step.get("with", {}).get("operation") == "semgrep-run":
            return step
    raise AssertionError("analyze-semgrep.yml has no SDK Semgrep run step")


def _governance_step(document: dict) -> dict:
    for step in document["jobs"]["analyze"]["steps"]:
        if step.get("id") == "gov":
            return step
    raise AssertionError("analyze-semgrep.yml has no governance resolver step")


class AnalyzeSemgrepEventClassTests(unittest.TestCase):
    def test_reusable_kernel_accepts_an_explicit_governance_event_class(self) -> None:
        document = _workflow()
        event_input = document["on"]["workflow_call"]["inputs"]["event"]
        assert event_input["required"] is False
        assert event_input["default"] == ""
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "EVENT_OVERRIDE: ${{ inputs.event }}" in text
        assert 'source_event="${EVENT_OVERRIDE:-${TRIGGER_EVENT}}"' in text

    def test_manual_dispatch_without_an_override_stays_a_nightly_canary(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "TRIGGER_EVENT: ${{ github.event_name }}" in text
        assert "schedule|workflow_dispatch|nightly)" in text
        assert 'event="nightly"' in text

    def test_explicit_event_precedes_the_manual_nightly_fallback(self) -> None:
        text = " ".join(WORKFLOW.read_text(encoding="utf-8").split())
        override = text.index("EVENT_OVERRIDE: ${{ inputs.event }}")
        fallback = text.index("schedule|workflow_dispatch|nightly)")
        assert override < fallback

    def test_semgrep_run_uses_the_core_staged_map_for_its_bounded_language(
        self,
    ) -> None:
        step = _semgrep_run_step(_workflow())
        assert step["with"]["identity-map"] == (
            "${{ steps.gov.outputs['identity-map-directory'] }}/"
            "${{ steps.language.outputs.language }}.yaml"
        )

    def test_governance_resolver_pin_exports_the_identity_map_output(self) -> None:
        step = _governance_step(_workflow())
        assert step["uses"] == (
            "Quantum-L9/l9-ci-core/.github/actions/resolve-governance"
            f"@{GOVERNANCE_RESOLVER_PIN}"
        )
        assert step["with"]["event-name"] == "${{ steps.contract.outputs.event }}"
