"""``python3 -m tools.l9_bindings <stage>``.

Stages: ``plan`` (report drift, write nothing), ``converge`` (open the pull
requests drift requires), ``discover`` (list the repositories that declare a
binding), ``validate`` (parse the declaration). Exit codes: 0 converged or
proposed, 3 drift remains, 2 blocked or remote failure, 1 invalid bindings.
"""

from __future__ import annotations

import sys

from .converge import main

if __name__ == "__main__":
    sys.exit(main())
