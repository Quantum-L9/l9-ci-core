"""Workflows must not carry an uncoupled or unlocked toolchain pin.

Two failures of the same shape live in workflow YAML, and neither is visible
from the action directories that own the pins:

* an inline ``pytest==X`` literal that no test compares to the lock. One was
  born at ``8.4.2`` in the very commit that bumped the consumer pin to
  ``9.1.1`` (ade33c3) and sat two majors adrift because nothing looked at it.
* a raw ``pip install "semgrep==X"``, which pins one package and lets pip
  re-resolve the rest of the closure per run — the hazard
  ``.github/actions/install-semgrep`` exists to refuse. It is especially easy
  to miss because Semgrep is then executed as a PATH-resolved subprocess of
  the SDK, so it never appears as a bare name in any workflow while still
  being an unlocked runtime dependency.

These scan every workflow rather than naming the offenders, so the next one
cannot be introduced uncoupled either.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
LOCK = ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"

LOCKED_SEMGREP_ACTION = (
    "Quantum-L9/l9-ci-core/.github/actions/install-semgrep@"
    "def55c54ff4ba654c2ebea088dde71db0b5f7135"
)


def workflow_sources() -> list[Path]:
    """Both spellings: GitHub loads a workflow from .yml or .yaml."""
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def significant_lines(path: Path) -> list[tuple[int, str]]:
    """Numbered non-comment lines.

    Comment prose explains why a construct is forbidden and would otherwise
    match a search for that construct's own name.
    """
    return [
        (number, line)
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if not line.lstrip().startswith("#")
    ]


class InlinePinTests(unittest.TestCase):
    def test_inline_pytest_pins_match_the_lock(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        inline = re.compile(r"pytest==([0-9][^\s\"']*)")
        found: list[tuple[str, int, str]] = []
        for path in workflow_sources():
            for number, line in significant_lines(path):
                match = inline.search(line)
                if match:
                    found.append((path.name, number, match.group(1)))

        self.assertTrue(
            found, "expected at least one inline pytest pin to guard against drift"
        )
        for name, number, version in found:
            with self.subTest(workflow=name, line=number):
                self.assertEqual(
                    lock["pytest"],
                    version,
                    f"{name}:{number} pins pytest=={version} but the lock says "
                    f"{lock['pytest']}; bump the lock and follow it here",
                )


class SemgrepInstallPathTests(unittest.TestCase):
    def test_no_workflow_installs_semgrep_with_raw_pip(self) -> None:
        pattern = re.compile(r"pip\s+install[^\n]*semgrep\s*==")
        offenders = [
            f"{path.name}:{number}: {line.strip()}"
            for path in workflow_sources()
            for number, line in significant_lines(path)
            if pattern.search(line)
        ]
        self.assertEqual(
            [],
            offenders,
            "install Semgrep through the hash-locked action; a bare "
            "`pip install semgrep==X` re-resolves the closure per run",
        )

    def test_every_semgrep_installing_workflow_uses_the_locked_action(self) -> None:
        referencing = {
            path.name
            for path in workflow_sources()
            if "install-semgrep@" in path.read_text(encoding="utf-8")
        }
        self.assertIn("org-ci.yml", referencing)
        self.assertIn("analyze-semgrep.yml", referencing)
        for name in sorted(referencing):
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIn(
                    LOCKED_SEMGREP_ACTION,
                    text,
                    f"{name} must pin install-semgrep to the immutable Core "
                    "integration revision that also supplies invoke-sdk",
                )
                self.assertNotRegex(
                    text,
                    r"install-semgrep@v\d",
                    f"{name}: install-semgrep is a first-commit pin, not a "
                    "moving-major reference",
                )


class ProviderProvenanceTests(unittest.TestCase):
    def test_live_sdk_runs_receive_the_verified_semgrep_executable(self) -> None:
        for name in ("analyze-semgrep.yml", "org-ci.yml"):
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text(encoding="utf-8")
                run_start = text.index("operation: semgrep-run")
                run_end = text.find("\n      - ", run_start)
                block = text[run_start : run_end if run_end != -1 else None]
                self.assertIn(
                    "provider-executable: ${{ steps.semgrep.outputs.executable }}",
                    block,
                    "semgrep-run must receive Core's verified executable rather "
                    "than resolve another installation from PATH",
                )


class GateResolutionTests(unittest.TestCase):
    """The resolution contract must not be revertible in silence.

    Restoring the ``repo-check`` leaf to a bare ``ruff check .`` is valid Make
    and passes every structural check, so without this nothing would notice
    the guarantee disappearing. The same applies to the CI lint job, which is
    the step that actually gates a merge.
    """

    REPO_MK = ROOT / "Repo.mk"
    SELF_CI = WORKFLOWS / "self-ci.yml"
    PREFLIGHT = "$(PYTHON) tools/check_toolchain_versions.py"

    def check_recipe(self) -> list[str]:
        text = self.REPO_MK.read_text(encoding="utf-8")
        match = re.search(r"(?m)^repo-check:.*\n((?:\t.*\n)+)", text)
        self.assertIsNotNone(match, "Repo.mk must implement repo-check")
        assert match is not None
        return [line.strip().lstrip("@") for line in match.group(1).splitlines()]

    def test_check_runs_the_preflight_first(self) -> None:
        self.assertEqual(
            self.PREFLIGHT,
            self.check_recipe()[0],
            "the toolchain preflight must run before any gate it protects",
        )

    def test_every_check_command_resolves_through_the_interpreter(self) -> None:
        for command in self.check_recipe():
            with self.subTest(command=command):
                self.assertTrue(
                    command.startswith("$(PYTHON) "),
                    "a bare executable lets PATH decide which code the gate "
                    "runs; resolve through the interpreter instead",
                )

    def test_ci_lint_job_does_not_invoke_bare_tools(self) -> None:
        bare = re.compile(r"^\s*run:\s*(ruff|mypy)\b")
        offenders = [
            f"self-ci.yml:{number}: {line.strip()}"
            for number, line in significant_lines(self.SELF_CI)
            if bare.search(line)
        ]
        self.assertEqual(
            [],
            offenders,
            "the lint job gates merges; a bare name there reintroduces the "
            "PATH ambiguity the check contract removes",
        )

    def test_ci_lint_job_runs_the_preflight(self) -> None:
        self.assertIn(
            "python tools/check_toolchain_versions.py",
            self.SELF_CI.read_text(encoding="utf-8"),
            "the merge-gating lint job must assert its toolchain too",
        )


if __name__ == "__main__":
    unittest.main()
