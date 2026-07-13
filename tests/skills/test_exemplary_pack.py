from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).parent.parent.parent
_PACK = _REPO / ".claude" / "skills" / "l9-pr-remediation"
_VALIDATOR = _PACK / "scripts" / "validate_exemplary_skill.py"


def test_pack_still_passes_exemplary_gate() -> None:
    # Guards the committed pack: any edit that breaks exemplary-tier evidence
    # (missing intelligence artifacts, dropped gate, tier downgrade) fails CI.
    result = subprocess.run([sys.executable, str(_VALIDATOR), str(_PACK)])
    assert result.returncode == 0
