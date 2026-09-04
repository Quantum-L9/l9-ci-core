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
# The installer is referenced by SHA, never by a floating tag. fa0ba1e found that
# every `install-consumer-ci@v2` reference resolved to nothing ("unable to find
# version `v2`") and SHA-pinned all four surfaces, but left this constant behind
# asserting the reference it had just removed -- so the assertion has demanded a
# form the workflows must not use ever since.
#
# Pinning is not merely the status quo to be matched: audit-pins-org.yml in
# Quantum-L9/.github rates an unpinned FIRST-PARTY ref HIGH, above an unpinned
# external one, so reintroducing a floating `@v2` would add HIGH findings. A v2
# tag does exist on this repository today, which is exactly why the assertion
# has to name the pinning requirement rather than the action reference alone --
# the tag resolving now would otherwise make a policy violation look correct.
INSTALLER_ACTION = "Quantum-L9/l9-ci-core/.github/actions/install-consumer-ci"
SHA_PINNED_INSTALLER = re.compile(rf"{re.escape(INSTALLER_ACTION)}@[0-9a-f]{{40}}\b")

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
        text = path.read_text(encoding="utf-8")
        assert INSTALLER_ACTION in text, (
            f"{path.relative_to(ROOT)} must call {INSTALLER_ACTION}"
        )


def test_surfaces_pin_the_installer_by_sha() -> None:
    """Every reference must be SHA-pinned, not tagged.

    Split from the call assertion above so a surface that calls the installer
    with a floating tag fails as a pinning violation, naming the offending
    reference, rather than as "does not call the installer".
    """
    for path in PYTHON_MYPY_SURFACES:
        text = _noncomment_code(path.read_text(encoding="utf-8"))
        references = re.findall(rf"{re.escape(INSTALLER_ACTION)}@\S+", text)
        assert references, f"{path.relative_to(ROOT)} must call {INSTALLER_ACTION}"
        for reference in references:
            assert SHA_PINNED_INSTALLER.fullmatch(reference), (
                f"{path.relative_to(ROOT)} references {reference}; the installer "
                "must be pinned to a 40-character commit SHA. audit-pins-org.yml "
                "rates an unpinned first-party ref HIGH, and `@v2` resolved to "
                "nothing for every consumer until fa0ba1e SHA-pinned it."
            )


def test_dependabot_does_not_own_consumer_ci_pins() -> None:
    text = DEPENDABOT.read_text(encoding="utf-8")
    assert not re.search(r'package-ecosystem:\s*"pip"', text), (
        "dependabot must not have a pip ecosystem on consumer CI pins"
    )
