"""PR-B2: deterministic risk classification (PR §6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from l9_ci_core.control_plane import risk

REPO_ROOT = Path(__file__).resolve().parents[2]
RISK_PATH = REPO_ROOT / ".github" / "governance" / "risk-tiers.yaml"


@pytest.fixture(scope="module")
def policy():
    return risk.load_risk_policy(RISK_PATH)


def test_docs_only_is_low(policy):
    assert risk.classify(policy, ["docs/guide.md"]).risk_tier == "low"
    assert risk.classify(policy, ["README.md"]).risk_tier == "low"


def test_generic_source_python_is_standard(policy):
    r = risk.classify(policy, ["lib/service.py"])
    assert r.risk_tier == "standard"
    assert r.reasons  # a reason is always present


def test_workflow_change_is_high(policy):
    assert risk.classify(policy, [".github/workflows/ci.yml"]).risk_tier == "high"


def test_gate_registry_is_regulated(policy):
    assert (
        risk.classify(policy, [".github/governance/gate-registry.yaml"]).risk_tier
        == "regulated"
    )


def test_evaluator_is_regulated(policy):
    # Matches both the regulated exact path and the high src/** glob; highest wins.
    assert (
        risk.classify(policy, ["src/l9_ci_core/control_plane/evaluator.py"]).risk_tier
        == "regulated"
    )


def test_unknown_diff_is_high(policy):
    r = risk.classify(policy, [], unknown_diff=True)
    assert r.risk_tier == "high"
    assert r.unknown_diff is True
    assert r.reasons == ["unknown_diff_fail_closed"]


def test_regulated_label_on_docs_raises_to_regulated(policy):
    r = risk.classify(policy, ["docs/x.md"], ["risk:regulated"])
    assert r.risk_tier == "regulated"


def test_high_label_raises_docs_to_high(policy):
    r = risk.classify(policy, ["docs/x.md"], ["security-sensitive"])
    assert r.risk_tier == "high"


def test_label_may_not_lower_path_risk(policy):
    # A workflow change is high; no label may lower it.
    r = risk.classify(policy, [".github/workflows/ci.yml"], ["risk:low"])
    assert r.risk_tier == "high"
    # A lower-ranked label on a regulated path leaves it regulated.
    r2 = risk.classify(policy, [".github/governance/risk-tiers.yaml"], ["risk:high"])
    assert r2.risk_tier == "regulated"


def test_mixed_docs_and_workflow_is_high(policy):
    r = risk.classify(policy, ["docs/x.md", ".github/workflows/ci.yml"])
    assert r.risk_tier == "high"


def test_empty_push_diff_failure_is_high(policy):
    # An empty diff produced by a failed collection is unknown_diff -> high.
    r = risk.classify_changed_files(
        policy, {"schema_version": "1.0", "files": [], "unknown_diff": True, "reason": "git_diff_failed"}
    )
    assert r.risk_tier == "high"


def test_malformed_changed_files_is_high_with_warning(policy):
    r = risk.classify_changed_files(policy, {"files": "not-a-list"})
    assert r.risk_tier == "high"
    assert r.unknown_diff is True
    assert r.warnings and "malformed" in r.warnings[0]


def test_classification_is_deterministic(policy):
    files = [".github/workflows/ci.yml", "docs/x.md", "lib/a.py"]
    a = risk.classify(policy, files, ["risk:high"]).to_dict()
    b = risk.classify(policy, files, ["risk:high"]).to_dict()
    assert a == b


def test_rank_matches_tier(policy):
    assert risk.classify(policy, ["docs/x.md"]).rank == 10
    assert risk.classify(policy, ["lib/a.py"]).rank == 20
    assert risk.classify(policy, [".github/workflows/ci.yml"]).rank == 30
    assert risk.classify(policy, [".github/governance/gate-registry.yaml"]).rank == 40
