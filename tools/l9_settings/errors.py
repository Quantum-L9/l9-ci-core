"""Settings engine failure classes.

``SettingsRefused`` (exit 1): the declaration or a precondition refuses the
run. ``SettingsUnreadable`` (exit 2): a declared setting could not be read
live, so nothing is applied (fail closed). ``SettingsInfrastructureError``
(exit 2): a tool, transport, or write failure.
"""

from __future__ import annotations


class SettingsError(Exception):
    """Base class for every settings engine failure."""


class SettingsRefused(SettingsError):
    """The declaration or a precondition refused the run. Exit code 1."""


class SettingsInfrastructureError(SettingsError):
    """A tool, transport, or write failure. Exit code 2."""
