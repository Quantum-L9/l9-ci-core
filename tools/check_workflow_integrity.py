#!/usr/bin/env python3
"""Fail-closed workflow integrity checker.

Scans a caller repository's GitHub Actions workflows and composite actions
recursively for fail-open constructs and untrusted dependency edges:

- ``continue-on-error: true`` on jobs or steps
- ``|| true`` / ``|| exit 0`` / ``; exit 0`` suffixes in run scripts
- ``SKIP=`` environment overrides for pre-commit beyond an explicit
  allowlist (gitleaks needs a secret unavailable to forks)
- malformed action references of the form ``@<40-hex-sha>v<semver>``
- remote ``uses:`` references not pinned to a full 40-character SHA
- composite actions that nest another ``Quantum-L9/l9-ci-core`` action

The checker is deterministic and has no LLM in its decision path. It
exits 0 when clean, 1 on any violation, and 2 on usage errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MALFORMED_ACTION_REF = re.compile(r"uses:\s*\S+@[0-9a-f]{40}v\d")
USES_REFERENCE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
CORE_ACTION_PREFIX = "Quantum-L9/l9-ci-core/.github/actions/"
CONTINUE_ON_ERROR = re.compile(r"^\s*continue-on-error:\s*true\b")
# `|| true` is fail-open only when it guards a whole command outcome; the
# widespread `grep ... || true` no-match idiom inside pipelines and command
# substitutions is benign (grep exits 1 on zero matches by design), so the
# checker exempts lines whose guarded command is grep or that occur inside
# a command substitution capture.
FAIL_OPEN_SHELL = re.compile(r"\|\|\s*(true|exit\s+0)\b|;\s*exit\s+0\s*$")
BENIGN_GREP_GUARD = re.compile(r"\bgrep\b[^|]*(\|[^|]+)*\|\|\s*true\b")
COMMAND_SUBSTITUTION = re.compile(r"\$\(|<\s*<\(")
SKIP_OVERRIDE = re.compile(r"\bSKIP=([A-Za-z0-9_,\-]+)")
ALLOWED_SKIPS = frozenset({"gitleaks"})


def check_file(path: Path, *, composite: bool = False) -> list[str]:
    violations: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        return [f"{path}: unreadable workflow file ({error})"]
    for number, line in enumerate(lines, start=1):
        stripped = line.split("#", 1)[0]
        if not stripped.strip():
            continue
        if MALFORMED_ACTION_REF.search(stripped):
            violations.append(
                f"{path}:{number}: malformed action reference (sha+tag fusion)"
            )
        reference_match = USES_REFERENCE.match(stripped)
        if reference_match:
            reference = reference_match.group(1).strip("\"'")
            if not reference.startswith("./"):
                target, separator, revision = reference.rpartition("@")
                if not separator or not FULL_SHA.fullmatch(revision):
                    violations.append(
                        f"{path}:{number}: remote action reference must use a "
                        "full lowercase 40-character SHA"
                    )
                if composite and target.startswith(CORE_ACTION_PREFIX):
                    violations.append(
                        f"{path}:{number}: composite action must be a local leaf; "
                        "nested Core action reference is forbidden"
                    )
        if CONTINUE_ON_ERROR.search(stripped):
            violations.append(
                f"{path}:{number}: continue-on-error is forbidden (fail-open)"
            )
        if FAIL_OPEN_SHELL.search(stripped) and not (
            BENIGN_GREP_GUARD.search(stripped) or COMMAND_SUBSTITUTION.search(stripped)
        ):
            violations.append(
                f"{path}:{number}: fail-open shell suffix (|| true / exit 0)"
            )
        for match in SKIP_OVERRIDE.finditer(stripped):
            skips = {token for token in match.group(1).split(",") if token}
            forbidden = sorted(skips - ALLOWED_SKIPS)
            if forbidden:
                violations.append(
                    f"{path}:{number}: forbidden SKIP override: "
                    f"{', '.join(forbidden)} (allowed: "
                    f"{', '.join(sorted(ALLOWED_SKIPS))})"
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows-dir", default=".github/workflows")
    parser.add_argument("--actions-dir", default=".github/actions")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Path prefixes to skip (repeatable).",
    )
    arguments = parser.parse_args()
    workflows = Path(arguments.workflows_dir)
    if not workflows.is_dir():
        print(f"workflows directory not found: {workflows}", file=sys.stderr)
        return 2
    actions = Path(arguments.actions_dir)
    excludes = tuple(arguments.exclude)
    violations: list[str] = []
    candidates = [
        *((path, False) for path in sorted(workflows.rglob("*.yml"))),
        *((path, False) for path in sorted(workflows.rglob("*.yaml"))),
    ]
    if actions.is_dir():
        candidates.extend((path, True) for path in sorted(actions.rglob("*.yml")))
        candidates.extend((path, True) for path in sorted(actions.rglob("*.yaml")))
    for path, composite in candidates:
        text = str(path)
        if any(text.startswith(prefix) for prefix in excludes):
            continue
        violations.extend(check_file(path, composite=composite))
    if violations:
        print("workflow integrity violations:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    scanned = f"{workflows}, {actions}" if actions.is_dir() else str(workflows)
    print(f"workflow/action integrity clean: {scanned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
