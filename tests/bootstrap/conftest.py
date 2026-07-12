import shutil
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO / ".github" / "scripts"))

_SCHEMAS_SRC = _REPO / "schemas"


@pytest.fixture(autouse=True)
def _stage_required_schemas(tmp_path):
    """Stage the shipped governance schemas into every test's ``tmp_path`` root.

    The CI gate CLI now treats governance schemas as required dependencies and
    fails closed when they are absent. Tests exercise the validators against
    ``tmp_path`` roots, so the real schemas must be present there — the correct
    fix per the security contract (provide schemas in the test root) rather than
    weakening production behavior with optional/graceful-degradation loading.
    """
    if _SCHEMAS_SRC.is_dir():
        dest = tmp_path / "schemas"
        if not dest.exists():
            shutil.copytree(_SCHEMAS_SRC, dest)
    yield
