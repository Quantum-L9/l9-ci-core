from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_audit_policy_declares_canonical_classes() -> None:
    policy = yaml.safe_load((ROOT / ".github/governance/audit-policy.yml").read_text())
    assert set(policy["canonical_pr_classes"]) == {
        "docs_only",
        "ci_workflow",
        "dependency_python",
        "app_code",
        "security",
        "compliance",
        "unknown_diff",
    }
    assert "security_sensitive" in policy["forbidden_pr_classes"]
    assert policy["comments"]["marker"] == "<!-- l9-ci-audit-marker: v1 -->"


def test_audit_baseline_semantics() -> None:
    baseline = json.loads((ROOT / ".github/governance/audit-baseline.json").read_text())
    assert baseline["semantics"] == {
        "in_baseline_and_not_touched": "advisory",
        "in_baseline_and_touched_dangerous": "blocking",
        "not_in_baseline_and_high_or_critical": "blocking",
        "not_in_baseline_and_low_medium": "advisory",
    }
    assert baseline["entries"] == []


def test_classifier_shared_spec_is_policy_source_of_truth() -> None:
    spec = yaml.safe_load((ROOT / ".github/governance/l9-ci-shared-spec.yaml").read_text())
    classifier = spec["classifier"]
    assert set(classifier["canonical_classes"]) == {
        "docs_only",
        "ci_workflow",
        "dependency_python",
        "app_code",
        "security",
        "compliance",
        "unknown_diff",
    }
    assert classifier["unknown_class"] == "unknown_diff"
    assert ".go" in classifier["taxonomy"]["app_code"]["suffixes"]
    assert ".rs" in classifier["taxonomy"]["app_code"]["suffixes"]
    assert classifier["taxonomy"]["security"]["class"] == "security"


def test_label_taxonomy_and_routing_policy_parse() -> None:
    labels = yaml.safe_load((ROOT / ".github/governance/label-taxonomy.yaml").read_text())
    routing = yaml.safe_load((ROOT / ".github/governance/ci-routing-policy.yaml").read_text())
    assert "area:transport" in labels["labels"]["area"]
    assert routing["routing_policy"]["unknown_diff"]["behavior"] == "fail_closed"


def test_quality_thresholds_policy_parse() -> None:
    thresholds = yaml.safe_load((ROOT / ".github/governance/quality-thresholds.yaml").read_text())
    assert thresholds["coverage"]["default"] == 80
    assert thresholds["coverage"]["l9_ci_sdk"] == 85
    assert thresholds["coverage"]["minimum_floor"] == 80
    assert thresholds["security"]["max_critical_findings"] == 0
    assert thresholds["rule_modes"]["transport_packet_contract"] == "blocking"


def test_audit_policy_references_thresholds() -> None:
    policy = yaml.safe_load((ROOT / ".github/governance/audit-policy.yml").read_text())
    assert policy["thresholds"]["source"] == ".github/governance/quality-thresholds.yaml"
    assert policy["thresholds"]["missing_thresholds"] == "fail_closed"


def test_comment_protocol_defines_agent_review_marker() -> None:
    proto = yaml.safe_load((ROOT / ".github/governance/comment-protocol.yaml").read_text())
    assert proto["markers"]["agent_review"] == "<!-- l9-agent-review-marker: v1 -->"
    assert proto["comment_limits"]["max_comment_chars"] == 65336
    assert proto["comment_limits"]["truncation_required"] is True
    assert "never_create_duplicate_persistent_comments" in proto["update_rule"]


def test_blocking_policy_review_promotions_start_empty_advisory_only() -> None:
    policy = yaml.safe_load((ROOT / ".github/governance/blocking-policy.yaml").read_text())
    # Agent Review Loop must be advisory-only until findings are explicitly promoted.
    assert policy["review_blocking_promotions"] == []
    assert "transport_packet_contract_break" in policy["hard_block_if_touched"]
    assert "diff_unknown" in policy["fail_closed"]
