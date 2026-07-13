import sys
from pathlib import Path

# Skill packs live under .claude/ which is not on sys.path by default. Add the
# l9-pr-remediation scripts dir so its validators import by module name, mirroring
# tests/bootstrap/conftest.py staging .github/scripts.
_REPO = Path(__file__).parent.parent.parent
_SCRIPTS = _REPO / ".claude" / "skills" / "l9-pr-remediation" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
