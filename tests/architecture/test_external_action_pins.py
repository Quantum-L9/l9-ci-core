from __future__ import annotations
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USES = re.compile(r"^\s*uses:\s*([^#\s]+)", re.MULTILINE)
FULL_SHA_REF = re.compile(r"^[^@]+@[0-9a-fA-F]{40}$")
# Toolchain installer is the only floating Core ref. Analysis/SDK stay SHA-pinned.
INSTALLER_V2 = "Quantum-L9/l9-ci-core/.github/actions/install-consumer-ci@v2"


class ExternalActionPinTests(unittest.TestCase):
    def test_external_actions_use_full_commit_shas(self) -> None:
        violations: list[str] = []
        for workflow in (ROOT / ".github").rglob("*.yml"):
            text = workflow.read_text(encoding="utf-8")
            for reference in USES.findall(text):
                if reference.startswith("./"):
                    continue
                if reference == INSTALLER_V2:
                    continue
                if not FULL_SHA_REF.fullmatch(reference):
                    violations.append(f"{workflow.relative_to(ROOT)}:{reference}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
