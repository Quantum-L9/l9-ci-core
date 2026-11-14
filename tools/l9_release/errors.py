"""Release engine failure classes.

``ReleaseRefused`` is a fail-closed precondition (dirty tree, version not
ahead, missing CHANGELOG section, existing exact tag elsewhere being moved…):
the repository state does not admit a release. ``ReleaseInfrastructureError``
is a tool, transport, or contract failure. The CLI maps them to distinct exit
codes so a workflow can tell a refusal from a broken runner.
"""

from __future__ import annotations


class ReleaseError(Exception):
    """Base class for every release engine failure."""


class ReleaseRefused(ReleaseError):
    """A precondition refused the release. Exit code 1."""


class ReleaseInfrastructureError(ReleaseError):
    """A tool, transport, or contract failure. Exit code 2."""
