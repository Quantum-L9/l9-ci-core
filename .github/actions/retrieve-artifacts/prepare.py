#!/usr/bin/env python3
"""Require a safe, empty artifact-download destination."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class PreparationError(RuntimeError):
    """The requested download destination is unsafe."""


def has_symlink_component(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return False
    current = boundary
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            return True
        if not current.exists():
            break
    return False


def main() -> int:
    try:
        value = os.environ.get("L9_DESTINATION", "").strip()
        if not value:
            raise PreparationError("L9_DESTINATION is required")
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve(
            strict=True
        )
        candidate = Path(value)
        lexical = candidate if candidate.is_absolute() else workspace / candidate
        lexical = Path(os.path.abspath(lexical))
        try:
            relative = lexical.relative_to(workspace)
        except ValueError as error:
            raise PreparationError("destination escapes GITHUB_WORKSPACE") from error
        if relative == Path("."):
            raise PreparationError("destination must not be GITHUB_WORKSPACE")
        if has_symlink_component(lexical, workspace):
            raise PreparationError("destination contains a symlink component")
        if lexical.exists():
            if not lexical.is_dir():
                raise PreparationError("destination is not a directory")
            try:
                next(lexical.iterdir())
            except StopIteration:
                pass
            else:
                raise PreparationError("destination must be empty before download")
        else:
            lexical.mkdir(parents=True)
        resolved = lexical.resolve(strict=True)
        try:
            resolved.relative_to(workspace)
        except ValueError as error:
            raise PreparationError("destination escapes GITHUB_WORKSPACE") from error
        return 0
    except (OSError, PreparationError) as error:
        print(f"retrieve-artifacts: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
