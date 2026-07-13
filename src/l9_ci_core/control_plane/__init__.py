"""L9 CI Core declarative gate control plane.

This subpackage is the importable home of the control-plane primitives. Only
dependency-free foundations (canonical JSON and digests) are re-exported at
package import time so that

    python -c "import l9_ci_core.control_plane"

succeeds in an environment installed with ``pip install --no-deps -e .`` — i.e.
without PyYAML/jsonschema present. Stage modules that need those libraries
(:mod:`registry`, :mod:`planner`, :mod:`evaluator`, ...) import them lazily
inside their functions, never at module import time.
"""

from .canonical_json import dumps_canonical, encode_canonical
from .digests import sha256_bytes, sha256_canonical

__all__ = [
    "dumps_canonical",
    "encode_canonical",
    "sha256_bytes",
    "sha256_canonical",
]
