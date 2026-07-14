from __future__ import annotations
from pathlib import Path


def repo_root(start=None) -> Path:
    return Path(start).resolve() if start else Path.cwd().resolve()


def safe_resolve(base: Path, rel) -> Path:
    base_real = base.resolve()
    # Pre-check symlink escape before full resolve
    candidate_unresolved = base / rel
    check = candidate_unresolved
    while check != check.parent:
        if check.is_symlink():
            real = check.resolve()
            try:
                real.relative_to(base_real)
            except ValueError as exc:
                raise ValueError(f"Symlink escape: {check} -> {real}") from exc
        check = check.parent
    candidate = candidate_unresolved.resolve()
    try:
        candidate.relative_to(base_real)
    except ValueError as exc:
        raise ValueError(f"Path escape: {rel!r} resolves outside {base_real}") from exc
    return candidate


def validate_output_path(dest, base: Path) -> Path:
    """Resolve an output path and guarantee it stays inside ``base``.

    Hardened: previously an absolute ``dest`` was returned unchecked, which
    allowed an output file to be written anywhere on disk. Now every
    destination -- absolute or relative -- is resolved and confirmed to live
    under the approved output root, raising ``ValueError`` on escape.
    """
    approved = Path(base).resolve()
    destination = Path(dest)
    if not destination.is_absolute():
        destination = approved / destination
    destination = destination.resolve(strict=False)
    try:
        destination.relative_to(approved)
    except ValueError as exc:
        raise ValueError(f"Output path escapes approved output root: {destination}") from exc
    return destination
