from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "classify_pr.py"
POLICY = ROOT / ".github" / "governance" / "l9-ci-shared-spec.yaml"

spec = importlib.util.spec_from_file_location("classify_pr", SCRIPT)
assert spec and spec.loader
classify_pr = importlib.util.module_from_spec(spec)
sys.modules["classify_pr"] = classify_pr
spec.loader.exec_module(classify_pr)


def policy() -> classify_pr.ClassifierPolicy:
    return classify_pr.load_policy(POLICY)


def test_docs_only_classification() -> None:
    result = classify_pr.classify(["README.md", "docs/usage.md"], policy())
    assert result.pr_class == "docs_only"


def test_ci_workflow_classification() -> None:
    result = classify_pr.classify([".github/workflows/ci.yml"], policy())
    assert result.pr_class == "ci_workflow"


def test_dependency_python_classification() -> None:
    result = classify_pr.classify(["pyproject.toml"], policy())
    assert result.pr_class == "dependency_python"


def test_security_priority_over_docs() -> None:
    result = classify_pr.classify(["docs/security.md", ".gitleaks.toml"], policy())
    assert result.pr_class == "security"


def test_compliance_classification() -> None:
    result = classify_pr.classify(["contracts/transport-packet.schema.json"], policy())
    assert result.pr_class == "compliance"


def test_app_code_classification() -> None:
    result = classify_pr.classify(["engine/handlers.py"], policy())
    assert result.pr_class == "app_code"


def test_unknown_diff_fails_closed() -> None:
    result = classify_pr.classify(["binary/blob.bin"], policy())
    assert result.pr_class == "unknown_diff"


def test_canonical_set_loaded_from_policy() -> None:
    assert classify_pr.load_policy(POLICY).canonical_classes == {
        "docs_only",
        "ci_workflow",
        "dependency_python",
        "app_code",
        "security",
        "compliance",
        "unknown_diff",
    }


def test_new_language_extension_can_be_added_by_yaml_only(tmp_path: Path) -> None:
    data = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    data["classifier"]["taxonomy"]["app_code"]["suffixes"].append(".zig")
    local_policy_path = tmp_path / "l9-ci-shared-spec.yaml"
    local_policy_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    loaded = classify_pr.load_policy(local_policy_path)
    result = classify_pr.classify(["src/transport_node.zig"], loaded)

    assert result.pr_class == "app_code"
    assert any("app_code" in reason for reason in result.reasons)


def test_priority_can_be_changed_by_yaml_only(tmp_path: Path) -> None:
    data = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    data["classifier"]["priority"] = [
        "dependency_python",
        "security",
        "compliance",
        "ci_workflow",
        "app_code",
        "docs_only",
    ]
    local_policy_path = tmp_path / "l9-ci-shared-spec.yaml"
    local_policy_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    loaded = classify_pr.load_policy(local_policy_path)
    result = classify_pr.classify(["pyproject.toml", ".gitleaks.toml"], loaded)

    assert result.pr_class == "dependency_python"


def test_policy_rejects_noncanonical_taxonomy_class(tmp_path: Path) -> None:
    data = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    data["classifier"]["taxonomy"]["app_code"]["class"] = "new_unapproved_class"
    local_policy_path = tmp_path / "bad.yaml"
    local_policy_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    try:
        classify_pr.load_policy(local_policy_path)
    except SystemExit as exc:
        assert "must be canonical" in str(exc)
    else:
        raise AssertionError("Expected malformed policy to fail closed")
