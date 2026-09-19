"""The central gate's Core-selected toolchain does not move on its own.

``org-ci.yml`` is the organization-required workflow: every governed
pull_request and merge_group evaluation runs it. The 2026-09-13 workflow audit
(F-005) recorded two moving inputs: a ``-latest`` runner label and an inline
``pip install --upgrade pip semgrep==X`` that pinned one package while letting
pip resolve the rest of the closure against whatever the runner image offered.

What Core controls, and what these assertions lock, is the toolchain it
selects: an explicit Ubuntu release family (never ``-latest``), the
setup-python action by full SHA, the interpreter by exact major.minor.patch
version (a ``3.N`` selector resolves to whichever patch the current runner
image preinstalls, which changes without a Core revision), and the Semgrep
install routed through the hash-locked ``install-semgrep`` action that ships
its lock inside the pinned Core revision. The GitHub-hosted runner image build
within that Ubuntu family is GitHub's to roll and is deliberately not claimed
here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
LOCKS = ROOT / ".github" / "actions" / "install-semgrep" / "locks"

INSTALL_SEMGREP_REF = re.compile(
    r"uses:\s*Quantum-L9/l9-ci-core/\.github/actions/install-semgrep@([0-9a-f]{40})"
)


def load() -> dict:
    return yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))


class RunnerFamilyTests(unittest.TestCase):
    """An explicit Ubuntu release family is selected; the image build is not."""

    def test_analyze_job_names_an_explicit_ubuntu_release_family(self) -> None:
        job = load()["jobs"]["analyze"]
        self.assertEqual(
            "ubuntu-24.04",
            str(job["runs-on"]),
            "the central gate must name an explicit Ubuntu release family; "
            "-latest would let a family jump re-base the fleet's evaluation",
        )

    def test_no_floating_runner_label_anywhere_in_the_entrypoint(self) -> None:
        text = ORG_CI.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?m)^\s*runs-on:\s*\S+-latest\b")

    def test_entrypoint_does_not_claim_a_fixed_runner_image_build(self) -> None:
        """GitHub rolls hosted image builds within a family; Core must say so."""
        text = ORG_CI.read_text(encoding="utf-8")
        runs_on = text.index("runs-on: ubuntu-24.04")
        comment = text[max(0, runs_on - 800) : runs_on]
        self.assertIn("GitHub still rolls hosted image builds", comment)
        self.assertNotRegex(text, r"(?i)immutable (runner )?image")


class ToolchainLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = ORG_CI.read_text(encoding="utf-8")
        self.steps = load()["jobs"]["analyze"]["steps"]

    def step(self, name_fragment: str) -> dict:
        for step in self.steps:
            if name_fragment in str(step.get("name", "")):
                return step
        raise AssertionError(f"no step named like {name_fragment!r}")

    def test_semgrep_is_installed_through_the_locked_core_action(self) -> None:
        match = INSTALL_SEMGREP_REF.search(self.text)
        self.assertIsNotNone(
            match, "org-ci.yml must use the pinned install-semgrep action"
        )
        step = self.step("Install Semgrep")
        self.assertEqual(
            "${{ steps.runtime.outputs['semgrep-version'] }}",
            step["with"]["semgrep-version"],
        )
        self.assertIn("steps.language.outputs.language != 'none'", step["if"])

    def test_no_inline_pip_install_of_semgrep_remains(self) -> None:
        self.assertNotRegex(self.text, r"pip install[^\n]*semgrep==")
        self.assertNotRegex(self.text, r"pip install --upgrade pip\b")

    def test_interpreter_is_pinned_before_the_locked_install(self) -> None:
        names = [str(step.get("name", "")) for step in self.steps]
        python = next(i for i, n in enumerate(names) if "locked Python" in n)
        semgrep = next(i for i, n in enumerate(names) if "Install Semgrep" in n)
        self.assertLess(python, semgrep)
        step = self.steps[python]
        self.assertRegex(str(step["uses"]), r"^actions/setup-python@[0-9a-f]{40}$")
        selected = str(step["with"]["python-version"])
        self.assertRegex(
            selected,
            r"^3\.[0-9]+\.[0-9]+$",
            "python-version must be an exact major.minor.patch; a 3.N selector "
            "lets setup-python float to a newer patch without a Core revision",
        )
        self.assertEqual("3.12.14", selected)

    def test_default_central_semgrep_version_has_a_lock(self) -> None:
        document = load()
        triggers = document[True] if True in document else document["on"]
        version = str(
            triggers["workflow_dispatch"]["inputs"]["semgrep-version"]["default"]
        )
        self.assertTrue(
            (LOCKS / f"semgrep-{version}.txt").is_file(),
            f"install-semgrep ships no lock for the default {version}",
        )


if __name__ == "__main__":
    unittest.main()
