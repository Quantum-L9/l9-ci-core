from __future__ import annotations
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USES = re.compile(r"^\s*uses:\s*([^#\s]+)", re.MULTILINE)
FULL_SHA_REF = re.compile(r"^[^@]+@[0-9a-fA-F]{40}$")
# Toolchain installer is the only floating Core ref besides the moving major.
INSTALLER_V2 = "Quantum-L9/l9-ci-core/.github/actions/install-consumer-ci@v2"
CORE_V1 = re.compile(
    r"^Quantum-L9/l9-ci-core/\.github/(?:actions|workflows)/[^@\s]+@v1$"
)


def _dbg(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    import json
    import time
    from pathlib import Path as _P

    payload = {
        "sessionId": "f6ca56",
        "runId": "post-fix",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    log = _P("/Users/ib-mac/Cursor-Governance/.cursor/debug-f6ca56.log")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    # #endregion


class ExternalActionPinTests(unittest.TestCase):
    def test_external_actions_use_full_commit_shas(self) -> None:
        violations: list[str] = []
        core_v1: list[str] = []
        for workflow in (ROOT / ".github").rglob("*.yml"):
            text = workflow.read_text(encoding="utf-8")
            rel = str(workflow.relative_to(ROOT))
            for reference in USES.findall(text):
                if reference.startswith("./"):
                    continue
                if reference == INSTALLER_V2:
                    continue
                if CORE_V1.fullmatch(reference):
                    core_v1.append(f"{rel}:{reference}")
                    continue
                if not FULL_SHA_REF.fullmatch(reference):
                    violations.append(f"{rel}:{reference}")
        analyze_v1 = [item for item in core_v1 if "analyze-semgrep.yml" in item]
        _dbg(
            "H1",
            "test_external_action_pins.py:core_v1",
            "Core self-refs classified as moving major @v1",
            {
                "analyze_v1_count": len(analyze_v1),
                "analyze_v1": analyze_v1,
                "violations": violations,
            },
        )
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
