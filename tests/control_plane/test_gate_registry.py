"""PR-B2: gate-registry loader and semantic validation (PR §5)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from l9_ci_core.control_plane import registry
from l9_ci_core.control_plane.digests import policy_digests

REPO_ROOT = Path(__file__).resolve().parents[2]
REG_PATH = REPO_ROOT / ".github" / "governance" / "gate-registry.yaml"

BOOTSTRAP_IDS = {
    "workflow/action-pins",
    "workflow/download-integrity",
    "dependencies/ci-lock",
    "workflow/contracts",
}


@pytest.fixture()
def parsed():
    _src, _sem, data = policy_digests(REG_PATH)
    return data


def _validate(data):
    return registry.validate_registry_data(data, root=REPO_ROOT)


def test_loads_and_has_exactly_the_four_bootstrap_gates():
    reg = registry.load_registry(REG_PATH)
    assert set(reg.gates) == BOOTSTRAP_IDS


def test_owner_layers_are_correct():
    reg = registry.load_registry(REG_PATH)
    assert reg.gate("workflow/action-pins").owner_layer == "l9_policy_runtime"
    assert reg.gate("workflow/download-integrity").owner_layer == "l9_policy_runtime"
    assert reg.gate("dependencies/ci-lock").owner_layer == "l9_policy_runtime"
    assert reg.gate("workflow/contracts").owner_layer == "l9_assurance"


def test_all_modes_blocking_and_evidence_required():
    reg = registry.load_registry(REG_PATH)
    for spec in reg.gates.values():
        assert spec.mode == "blocking"
        assert spec.evidence_required is True


def test_workflow_contracts_is_always_run():
    reg = registry.load_registry(REG_PATH)
    assert reg.gate("workflow/contracts").always_run is True
    assert reg.gate("workflow/action-pins").always_run is False


def test_semantic_digest_is_deterministic():
    assert registry.load_registry(REG_PATH).semantic_digest == (
        registry.load_registry(REG_PATH).semantic_digest
    )


def test_semantic_digest_ignores_whitespace_and_comments(tmp_path):
    original = REG_PATH.read_text(encoding="utf-8")
    reworded = "# an added comment line\n" + original + "\n\n"
    alt = tmp_path / "gate-registry.yaml"
    alt.write_text(reworded, encoding="utf-8")
    a = registry.load_registry(REG_PATH, root=REPO_ROOT)
    b = registry.load_registry(alt, root=REPO_ROOT)
    assert a.source_digest != b.source_digest
    assert a.semantic_digest == b.semantic_digest


def test_unknown_command_key_rejected(parsed):
    parsed["gates"]["workflow/action-pins"]["executor"]["command_key"] = "rm_rf_slash"
    with pytest.raises(registry.RegistryError, match="unknown command key"):
        _validate(parsed)


def test_missing_referenced_schema_rejected(parsed):
    parsed["gates"]["workflow/action-pins"]["canonical_result_schema"] = (
        "schemas/does-not-exist.schema.json"
    )
    with pytest.raises(registry.RegistryError, match="referenced schema missing"):
        _validate(parsed)


def test_nonpositive_timeout_rejected(parsed):
    parsed["gates"]["workflow/action-pins"]["timeout_minutes"] = 0
    with pytest.raises(registry.RegistryError):
        _validate(parsed)


def test_out_of_bound_timeout_rejected(parsed):
    parsed["gates"]["workflow/action-pins"]["timeout_minutes"] = 99
    with pytest.raises(registry.RegistryError, match="outside"):
        _validate(parsed)


def test_retired_gate_with_active_tiers_rejected(parsed):
    parsed["gates"]["workflow/action-pins"]["lifecycle"]["status"] = "retired"
    with pytest.raises(registry.RegistryError, match="retired"):
        _validate(parsed)


def test_gate_absent_from_pr_a_schema_rejected(parsed):
    new = copy.deepcopy(parsed["gates"]["workflow/action-pins"])
    parsed["gates"]["custom/extra"] = new
    with pytest.raises(registry.RegistryError, match="absent from PR-A"):
        _validate(parsed)


def test_base_gate_removal_rejected(parsed):
    del parsed["gates"]["workflow/action-pins"]
    with pytest.raises(registry.RegistryError, match="removed or renamed"):
        _validate(parsed)


def test_base_mode_downgrade_rejected(parsed):
    # advisory does not require evidence, so only the bootstrap invariant fires.
    parsed["gates"]["workflow/action-pins"]["mode"] = "advisory"
    parsed["gates"]["workflow/action-pins"]["evidence_required"] = False
    with pytest.raises(registry.RegistryError, match="downgraded from blocking"):
        _validate(parsed)


def test_disabling_contracts_always_run_rejected(parsed):
    parsed["gates"]["workflow/contracts"]["selection"]["always_run"] = False
    with pytest.raises(registry.RegistryError, match="always-run"):
        _validate(parsed)


def test_blocking_without_evidence_rejected(parsed):
    # A non-bootstrap-invariant path: flip evidence off while staying blocking
    # on a gate; the blocking/evidence rule fires before invariants.
    parsed["gates"]["workflow/action-pins"]["evidence_required"] = False
    with pytest.raises(registry.RegistryError, match="must require evidence"):
        _validate(parsed)
