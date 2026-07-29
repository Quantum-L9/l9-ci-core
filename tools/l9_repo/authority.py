from __future__ import annotations

from pathlib import Path
from typing import Any


class AuthorityError(RuntimeError):
    """Raised when release identity or authority derivation drifts."""


def validate_authority(root: Path, config: dict[str, Any]) -> None:
    metadata = config["metadata"]
    authority = config["authority"]
    version = metadata["artifact_version"]
    artifact_id = metadata["artifact_id"]
    errors: list[str] = []

    required_identity = {
        "README.md": (artifact_id, version),
        "AUTHORITY.md": (artifact_id, version),
        "MANIFEST.md": (artifact_id, version),
    }
    for relative, tokens in required_identity.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"missing identity document: {relative}")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for token in tokens:
            if token not in content:
                errors.append(
                    f"{relative} does not declare authoritative token {token!r}"
                )

    for relative in authority["derived_documents"]:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            errors.append(f"missing derived document: {relative}")

    dependencies = authority["dependency_manifests"]
    for relative in dependencies["component_bundled"]:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            errors.append(f"missing dependency manifest: {relative}")
    if (root / "AGENTS.md").is_file():
        for relative in dependencies["target_required"]:
            path = root / relative
            if path.is_symlink() or not path.is_file():
                errors.append(f"missing target dependency manifest: {relative}")

    component_authority = authority["component_authority"]
    component_schema = authority["component_schema"]
    if component_authority != ".l9/repo-workflow.json":
        errors.append("component authority path drift")
    if component_schema != ".l9/repo-workflow.schema.json":
        errors.append("component schema path drift")

    if errors:
        raise AuthorityError("; ".join(errors))
