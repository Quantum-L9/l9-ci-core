from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import validate_run_report as vrr

_REPO = Path(__file__).parent.parent.parent
_FIXTURES = _REPO / "tests" / "fixtures" / "run-report"
_SCRIPT = _REPO / ".claude" / "skills" / "l9-pr-remediation" / "scripts" / "validate_run_report.py"

_INVALID = [
    "invalid-multi-push.json",
    "invalid-not-green.json",
    "invalid-missing-reason.json",
]


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


_VALID = ["valid-basic.json", "valid-multisource.json"]


@pytest.mark.parametrize("fixture", _VALID)
def test_valid_report_has_no_errors(fixture: str) -> None:
    assert vrr.validate(_load(fixture)) == []


@pytest.mark.parametrize("fixture", _VALID)
def test_valid_report_exits_zero(fixture: str) -> None:
    result = subprocess.run([sys.executable, str(_SCRIPT), str(_FIXTURES / fixture)])
    assert result.returncode == 0


@pytest.mark.parametrize("fixture", _INVALID)
def test_invalid_report_has_errors(fixture: str) -> None:
    # Hard invariants fire with OR without jsonschema installed.
    assert vrr.validate(_load(fixture))


@pytest.mark.parametrize("fixture", _INVALID)
def test_invalid_report_exits_one(fixture: str) -> None:
    result = subprocess.run([sys.executable, str(_SCRIPT), str(_FIXTURES / fixture)])
    assert result.returncode == 1


def test_unparseable_input_exits_two(tmp_path: Path) -> None:
    bad = tmp_path / "not.json"
    bad.write_text("{ not json", encoding="utf-8")
    result = subprocess.run([sys.executable, str(_SCRIPT), str(bad)])
    assert result.returncode == 2
