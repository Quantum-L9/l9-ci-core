from __future__ import annotations

from pathlib import Path

import yaml

# This test file lives at <repo>/tests/, so the repo root is parents[1].
# (Previously assumed a nested l9-ci-core/ dir, which fails in a standard checkout.)
CORE = Path(__file__).resolve().parents[1]

RULE_FILES = [
    CORE / ".semgrep/l9-transport.yml",
    CORE / ".semgrep/l9-routing.yml",
    CORE / ".semgrep/l9-logging.yml",
    CORE / ".semgrep/l9-handler-signature.yml",
]


def test_ast_semgrep_rules_parse_and_have_shadow_metadata() -> None:
    ids: set[str] = set()
    for path in RULE_FILES:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["rules"], path
        for rule in data["rules"]:
            metadata = rule["metadata"]
            assert metadata["mode"] == "shadow"
            assert metadata["autofix_safe"] is False
            assert metadata["l9_rule_id"].startswith("AST-")
            ids.add(metadata["l9_rule_id"])
    assert "AST-TRANSPORT-001" in ids
    assert "AST-ROUTING-001" in ids
    assert "AST-LOGGING-001" in ids
    assert "AST-HANDLER-001" in ids


def test_rule_modes_register_all_ast_rules_as_shadow() -> None:
    rule_modes = yaml.safe_load((CORE / ".github/governance/rule-modes.yaml").read_text(encoding="utf-8"))
    rules = rule_modes["rules"]
    for path in RULE_FILES:
        for rule in yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]:
            assert rules[rule["metadata"]["l9_rule_id"]] == "shadow"


def test_ast_fixture_inventory_has_good_and_bad_examples() -> None:
    fixture_root = CORE / "tests/fixtures/semgrep"
    expected = [
        "good/transport_packet_good.py",
        "bad/packet_envelope_bad.py",
        "good/gate_routing_good.py",
        "bad/direct_node_dispatch_bad.py",
        "good/logging_good.py",
        "bad/logging_secret_bad.py",
        "good/handler_signature_good.py",
        "bad/handler_signature_bad.py",
    ]
    for rel in expected:
        assert (fixture_root / rel).is_file(), rel


def test_audit_policy_declares_ast_governance_shadow_only() -> None:
    policy = yaml.safe_load((CORE / ".github/governance/audit-policy.yml").read_text(encoding="utf-8"))
    ast = policy["ast_governance"]
    assert ast["status"] == "shadow_only"
    assert ast["autonomous_repair"] == "disabled"
    assert ".semgrep/l9-transport.yml" in ast["rule_files"]
