#!/usr/bin/env python3
"""The toolchain that runs must be the toolchain that was pinned.

``.l9/repo-workflow.json`` resolves the check gate through the ``@python``
sentinel, so ruff and mypy are imported from the workspace interpreter rather
than found on ``PATH``. That removes the ambiguity this check used to have to
detect: a shadowing ``ruff`` earlier on ``PATH`` can no longer decide which
code the gate executes.

One residue survives that change. The interpreter itself may have been
provisioned from something other than the pin files — a stale virtualenv, a
partial install, a hand-upgraded package. Resolution is then unambiguous and
still wrong. This module closes that gap by asking the *running* interpreter
what it actually has:

    importlib.metadata.version("ruff")   # what this interpreter will import
    toolchain-lock.json                  # what the repository pinned

Reading metadata is necessary but not sufficient. The gate runs ``-m <tool>``
with the repository root at the front of ``sys.path``; this module runs as a
script from ``tools/``, so metadata proves what is *installed*, not what ``-m``
would *import*. A plain ``ruff/`` package at the repository root wins for the
gate and is invisible to metadata — the same shadowing defect, moved from
``PATH`` to ``sys.path``. So each module is also resolved under the gate's own
search path and confirmed to belong to the pinned distribution.

The lock is read, never written. ``toolchain-lock.json`` is the canonical
owner of tool versions (``tests/actions/test_install_consumer_ci.py`` binds
every other copy to it); this module is a reader, so it cannot become a second
place a version is declared.

Fail-closed by construction: a missing distribution, an unreadable version, or
an unreadable lock is a violation, not a skip. There is deliberately no
lenient mode and no environment escape — a gate that can be told to pass is
not a gate.

    python3 tools/check_toolchain_versions.py [--root PATH] [--json]

Exit codes: ``0`` conforming, ``2`` a violation, ``3`` the lock could not be
read.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import pathlib
import sys
from typing import Any

#: Canonical version owner. Core-owned, and already the hub every other pin
#: file is asserted against.
LOCK = (
    pathlib.Path(".github") / "actions" / "install-consumer-ci" / "toolchain-lock.json"
)

#: Distributions the check gate imports through ``@python -m``. `pytest` and
#: `biome` are in the lock but are not executed by this repository's gates
#: (the test verb runs `unittest`), so asserting them here would claim a
#: property this process cannot observe.
GATE_DISTRIBUTIONS = ("ruff", "mypy")


class ToolchainError(RuntimeError):
    """The lock could not be read, so conformance is undeterminable."""


def _distribution_location(name: str) -> pathlib.Path | None:
    """Where the installed distribution lives, or None when absent."""
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    try:
        # Public API. `_path` is a private attribute that silently disappears
        # on a stdlib rename, degrading every diagnostic with nothing failing.
        located = distribution.locate_file("")
    except (AttributeError, NotImplementedError, TypeError, ValueError, OSError):
        # A Distribution is free not to implement `locate_file`, and a custom
        # finder may return something unusable. Those are the readings this
        # helper can legitimately fail to make, and None is reported as
        # "not installed" by the caller. Anything else is a bug in this
        # process, and a preflight gate should die on it rather than hand
        # back a quiet None that reads as a clean resolution.
        return None
    try:
        return pathlib.Path(str(located)).resolve()
    except OSError:
        return None


def _shadowing_module(name: str, gate_cwd: pathlib.Path) -> str | None:
    """Return the path `-m <name>` would import, when it is not the pinned one.

    The gate runs `@python -m <tool>` with cwd at the repository root, and
    `-m` puts that cwd at the front of `sys.path`. This module runs as a
    script, so its own `sys.path[0]` is `tools/` — a different search path.
    Reading distribution metadata alone therefore proves what is *installed*,
    not what `-m` will *import*: a plain `ruff/` package at the repository
    root wins for the gate and is invisible here.

    That is the same defect this check exists to prevent, moved from PATH to
    sys.path, so resolve the module under the gate's search path and confirm
    it belongs to the pinned distribution.
    """
    distribution_root = _distribution_location(name)
    if distribution_root is None:
        return None  # absence is reported by the version comparison

    original = list(sys.path)
    try:
        sys.path.insert(0, str(gate_cwd))
        importlib.invalidate_caches()
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            return f"{name} is not importable under the gate's sys.path"
        if spec is None:
            return f"{name} is not importable under the gate's sys.path"
        origin = spec.origin
        if origin in (None, "built-in", "frozen"):
            search = list(getattr(spec, "submodule_search_locations", ()) or ())
            if not search:
                return None
            origin = search[0]
        resolved = pathlib.Path(origin).resolve()
        if distribution_root in resolved.parents or resolved == distribution_root:
            return None
        return str(resolved)
    finally:
        sys.path[:] = original
        importlib.invalidate_caches()


def load_lock(root: pathlib.Path, relative: pathlib.Path = LOCK) -> dict[str, str]:
    # A check with nothing to assert passes vacuously, which is the same
    # failure as a check that cannot fail. If this ever becomes empty the gate
    # would silently stop verifying anything, so refuse rather than return an
    # empty conformance.
    if not GATE_DISTRIBUTIONS:
        raise ToolchainError(
            "no gate distributions are declared; a check that asserts nothing "
            "is not a passing check"
        )
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise ToolchainError(f"missing toolchain lock: {relative}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolchainError(f"{relative} is not readable JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ToolchainError(f"{relative} must be a JSON object")
    pins: dict[str, str] = {}
    for name in GATE_DISTRIBUTIONS:
        pinned = raw.get(name)
        if not isinstance(pinned, str) or not pinned.strip():
            raise ToolchainError(f"{relative} declares no {name} version")
        pins[name] = pinned.strip()
    return pins


def check(root: pathlib.Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Compare the running interpreter against the lock. Returns (violations, report)."""
    pins = load_lock(root)
    violations: list[str] = []
    report: list[dict[str, Any]] = []
    for name, expected in sorted(pins.items()):
        installed_at = _distribution_location(name)
        location = str(installed_at) if installed_at is not None else "not installed"

        # What `-m <name>` would actually import, under the gate's sys.path.
        shadow = _shadowing_module(name, root)
        if shadow is not None:
            violations.append(
                f"{name}: `-m {name}` would import {shadow}, which is not part "
                f"of the installed distribution at {location}. The gate would "
                f"run that instead of the pinned {expected}."
            )
        try:
            observed: str | None = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            observed = None
        except Exception as error:  # noqa: BLE001 - any read failure is a violation
            violations.append(
                f"{name}: version is unreadable ({error}); an undeterminable "
                f"toolchain version is not a pass"
            )
            report.append(
                {
                    "distribution": name,
                    "expected": expected,
                    "observed": None,
                    "location": location,
                    "conforming": False,
                }
            )
            continue

        conforming = observed == expected
        if observed is None:
            violations.append(
                f"{name}: not installed in this interpreter ({sys.executable}); "
                f"the check gate imports it through `@python -m {name}` and "
                f"the lock pins {expected}"
            )
        elif not conforming:
            violations.append(
                f"{name}: interpreter has {observed}, lock pins {expected} "
                f"(imported from {location}; interpreter {sys.executable})"
            )
        report.append(
            {
                "distribution": name,
                "expected": expected,
                "observed": observed,
                "location": location,
                "conforming": conforming,
            }
        )
    return violations, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert the running interpreter's toolchain matches the lock"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    try:
        violations, report = check(root)
    except ToolchainError as error:
        print(f"check-toolchain-versions: {error}", file=sys.stderr)
        return 3

    if args.json:
        print(
            json.dumps(
                {
                    "schema": "l9.toolchain-version-conformance/v1",
                    "interpreter": sys.executable,
                    "conforming": not violations,
                    "distributions": report,
                    "violations": violations,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for entry in report:
            state = "ok" if entry["conforming"] else "MISMATCH"
            print(
                f"{state:8} {entry['distribution']}: "
                f"expected {entry['expected']}, observed {entry['observed']}"
            )

    if violations:
        for violation in violations:
            print(f"check-toolchain-versions: {violation}", file=sys.stderr)
        print(
            "check-toolchain-versions: the gate would run a toolchain the "
            "repository did not pin. Reprovision this interpreter from "
            "requirements-repo-runtime.txt (`make setup`); note that a tool "
            "earlier on PATH is no longer the cause, because the gate "
            "resolves through the interpreter.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
