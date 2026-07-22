from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"

REQUIRED_WORKFLOWS = {
    "self-ci.yml",
    "sdk-contract-check.yml",
    "normalize-semgrep-report.yml",
    "governance-ci.yml",
    "profile-normalize-semgrep.yml",
    "publish-analysis.yml",
    "analyze-semgrep.yml",
    "release-validation.yml",
}

_USES = re.compile(r"^\s*uses:\s*(?P<ref>\S+)")
_PINNED = re.compile(r"[^@\s]+@[0-9a-fA-F]{40}")


class PhaseScopeTests(unittest.TestCase):
    def test_required_control_plane_workflows_exist(self) -> None:
        actual = {path.name for path in WORKFLOWS.glob("*.yml")}
        missing = REQUIRED_WORKFLOWS - actual
        self.assertEqual(
            set(), missing, f"missing control-plane workflows: {sorted(missing)}"
        )

    def test_every_external_action_is_pinned_by_sha(self) -> None:
        offenders: list[str] = []
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            for number, line in enumerate(
                workflow.read_text(encoding="utf-8").splitlines(), start=1
            ):
                match = _USES.match(line)
                if not match:
                    continue
                ref = match.group("ref").split("#", 1)[0].strip()
                if ref.startswith("./"):
                    continue
                if not _PINNED.fullmatch(ref):
                    offenders.append(f"{workflow.name}:{number}:{ref}")
        self.assertEqual([], offenders, f"unpinned external action refs: {offenders}")

    def test_phase_4_actions_exist(self) -> None:
        required = {
            "render-publication",
            "publish-check",
            "validate-release",
            "route-artifacts",
            "build-artifact-manifest",
            "invoke-sdk",
        }
        actual = {
            path.name for path in (ROOT / ".github/actions").iterdir() if path.is_dir()
        }
        self.assertTrue(required.issubset(actual))


if __name__ == "__main__":
    unittest.main()
