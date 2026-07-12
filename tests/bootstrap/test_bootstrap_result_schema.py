from __future__ import annotations
import pytest
from l9_bootstrap.models import GateResult, ResultStatus, Finding

def test_passed_with_no_violations_is_valid():
    r = GateResult("workflow/action-pins", ResultStatus.passed)
    r.finalize()
    assert r.result == ResultStatus.passed

def test_passed_with_violation_raises():
    r = GateResult("workflow/action-pins", ResultStatus.passed)
    r.add_violation("CODE", "msg")
    with pytest.raises(ValueError): r.finalize()

def test_failed_with_violation_is_valid():
    r = GateResult("workflow/action-pins", ResultStatus.failed)
    r.add_violation("CODE", "msg")
    r.finalize()

def test_failed_with_no_violation_raises():
    r = GateResult("workflow/action-pins", ResultStatus.failed)
    with pytest.raises(ValueError): r.finalize()

def test_error_with_violation_is_valid():
    r = GateResult("workflow/action-pins", ResultStatus.error)
    r.add_violation("EXECUTION_ERROR", "broke")
    r.finalize()

def test_error_with_no_violation_raises():
    r = GateResult("workflow/action-pins", ResultStatus.error)
    with pytest.raises(ValueError): r.finalize()

def test_to_dict_schema_version():
    r = GateResult("workflow/action-pins", ResultStatus.passed)
    assert r.to_dict()["schema_version"] == "1.0"

def test_warning_does_not_affect_passed():
    r = GateResult("workflow/action-pins", ResultStatus.passed)
    r.add_warning("WARN", "w")
    r.finalize()
    assert r.result == ResultStatus.passed

def test_finding_to_dict_omits_none():
    f = Finding(code="X", message="m")
    d = f.to_dict()
    assert "path" not in d and "line" not in d
