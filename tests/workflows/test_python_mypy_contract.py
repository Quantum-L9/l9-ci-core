"""Workflow-level contract tests for the Python mypy convergence.

Type checking is a required, blocking gate with explicit requiredness and
repository-owned configuration: no global ``--ignore-missing-imports``, no
silent conversion of failures into a passing notice, and no global Pydantic
plugin. Consumer tool pins live in the install-consumer-ci action.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PR_PIPELINE = ROOT / ".github" / "workflows" / "pr-pipeline.yml"
PY_PRESET = ROOT / "presets" / "python" / ".github" / "workflows" / "l9-lint-test.yml"
PY_STARTER = ROOT / "starter-workflows" / "python" / "l9-lint-test.yml"
LINT_TEST_TEMPLATE = ROOT / "docs" / "templates" / "l9-lint-test.yml"
CONSUMER_CI_PINS = (
    ROOT
    / ".github"
    / "actions"
    / "install-consumer-ci"
    / "requirements-consumer-ci.txt"
)
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
INSTALLER = "install-consumer-ci@v2"

PYTHON_MYPY_SURFACES = (PR_PIPELINE, PY_PRESET, PY_STARTER, LINT_TEST_TEMPLATE)


def _noncomment_code(text: str) -> str:
    # Strip shell/YAML `#` comments so prose that *names* a flag (e.g. "no
    # global --ignore-missing-imports") is not mistaken for an invocation.
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_no_surface_uses_global_ignore_missing_imports() -> None:
    for path in PYTHON_MYPY_SURFACES:
        code = _noncomment_code(path.read_text(encoding="utf-8"))
        assert "--ignore-missing-imports" not in code, (
            f"{path.relative_to(ROOT)} passes a global --ignore-missing-imports; "
            "repository-owned mypy config must decide per-module import handling"
        )


def test_pr_pipeline_declares_explicit_required_mypy_input() -> None:
    text = PR_PIPELINE.read_text(encoding="utf-8")
    assert "mypy-required:" in text, "pr-pipeline must expose a mypy-required input"
    # The input must default to blocking (required). Scan to the next input key
    # so the window always includes this input's `default:` line.
    block = text.split("mypy-required:", 1)[1]
    block = re.split(r"(?m)^      \w[\w-]*:", block, maxsplit=1)[0]
    assert re.search(r"default:\s*true", block), (
        "mypy-required must default to true (blocking)"
    )


def test_pr_pipeline_mypy_is_blocking_not_silently_swallowed() -> None:
    text = PR_PIPELINE.read_text(encoding="utf-8")
    # The old always-on swallow must be gone.
    assert "non-blocking in the v1 compat layer" not in text, (
        "pr-pipeline must not silently convert required mypy failures to a notice"
    )
    # A blocking invocation (bare `mypy "$SOURCE_DIR"` with no fail-open) exists.
    assert re.search(r'(?m)^\s*mypy "\$SOURCE_DIR"\s*$', text), (
        "pr-pipeline must run mypy as a blocking step when required"
    )
    # No fail-open `|| true` / `|| exit 0` on any mypy line.
    for line in text.splitlines():
        if "mypy " in line:
            assert not re.search(r"\|\|\s*(true|exit\s+0)\b", line), (
                f"fail-open mypy line: {line.strip()}"
            )


def test_consumer_ci_pins_manifest_exists_and_pins_mypy() -> None:
    assert CONSUMER_CI_PINS.is_file(), "action pin file must exist"
    text = CONSUMER_CI_PINS.read_text(encoding="utf-8")
    assert re.search(r"(?m)^mypy==", text), "consumer CI pins must pin mypy exactly"
    assert re.search(r"(?m)^ruff==", text)
    assert re.search(r"(?m)^pytest==", text)


def test_surfaces_call_the_installer_action() -> None:
    for path in PYTHON_MYPY_SURFACES:
        assert INSTALLER in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(ROOT)} must call {INSTALLER}"
        )


def test_dependabot_does_not_own_consumer_ci_pins() -> None:
    text = DEPENDABOT.read_text(encoding="utf-8")
    assert not re.search(r'package-ecosystem:\s*"pip"', text), (
        "dependabot must not have a pip ecosystem on consumer CI pins"
    )
