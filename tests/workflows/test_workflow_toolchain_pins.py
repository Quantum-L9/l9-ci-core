"""Workflows and Core self-host leaves must use a locked toolchain."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
LOCK = ROOT / ".github" / "actions" / "install-consumer-ci" / "toolchain-lock.json"
REPO_MK = ROOT / "Repo.mk"
LOCKED_SEMGREP_ACTION = "Quantum-L9/l9-ci-core/.github/actions/install-semgrep@673a3e4c82021809af32baac7571fde5e1059d3b"


def workflow_sources() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def significant_lines(path: Path) -> list[tuple[int, str]]:
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
        found = [
            (path.name, number, match.group(1))
            for path in workflow_sources()
            for number, line in significant_lines(path)
            if (match := inline.search(line))
        ]
        self.assertTrue(found)
        for name, number, version in found:
            with self.subTest(workflow=name, line=number):
                self.assertEqual(lock["pytest"], version)


class SemgrepInstallPathTests(unittest.TestCase):
    def test_no_workflow_installs_semgrep_with_raw_pip(self) -> None:
        pattern = re.compile(r"pip\s+install[^\n]*semgrep\s*==")
        offenders = [
            f"{path.name}:{number}: {line.strip()}"
            for path in workflow_sources()
            for number, line in significant_lines(path)
            if pattern.search(line)
        ]
        self.assertEqual([], offenders)

    def test_every_semgrep_installing_workflow_uses_the_locked_action(self) -> None:
        referencing = {
            path.name
            for path in workflow_sources()
            if "install-semgrep@" in path.read_text(encoding="utf-8")
        }
        self.assertIn("org-ci.yml", referencing)
        self.assertIn("analyze-semgrep.yml", referencing)
        for name in referencing:
            self.assertIn(
                LOCKED_SEMGREP_ACTION, (WORKFLOWS / name).read_text(encoding="utf-8")
            )


class GateResolutionTests(unittest.TestCase):
    """Core's self-host check implementation owns tool resolution, not V2 JSON."""

    SELF_CI = WORKFLOWS / "self-ci.yml"

    def test_repo_mk_runs_preflight_before_static_tools(self) -> None:
        text = REPO_MK.read_text(encoding="utf-8")
        section = re.search(r"(?ms)^repo-check:\n(?P<body>(?:\t.*\n)+)", text)
        self.assertIsNotNone(section)
        assert section is not None
        body = section.group("body")
        self.assertLess(
            body.index("check_toolchain_versions.py"), body.index("-m ruff")
        )
        self.assertLess(body.index("-m ruff"), body.index("-m mypy"))

    def test_repo_mk_resolves_static_tools_through_the_interpreter(self) -> None:
        body = re.search(
            r"(?ms)^repo-check:\n(?P<body>(?:\t.*\n)+)",
            REPO_MK.read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(body)
        assert body is not None
        self.assertNotRegex(body.group("body"), r"^\s*\t@?(ruff|mypy)\b")
        self.assertIn("$(PYTHON) -m ruff", body.group("body"))
        self.assertIn("$(PYTHON) -m mypy", body.group("body"))

    def test_ci_lint_job_does_not_invoke_bare_tools(self) -> None:
        offenders = [
            f"self-ci.yml:{number}: {line.strip()}"
            for number, line in significant_lines(self.SELF_CI)
            if re.search(r"^\s*run:\s*(ruff|mypy)\b", line)
        ]
        self.assertEqual([], offenders)

    def test_ci_lint_job_runs_the_preflight(self) -> None:
        self.assertIn(
            "python tools/check_toolchain_versions.py",
            self.SELF_CI.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
