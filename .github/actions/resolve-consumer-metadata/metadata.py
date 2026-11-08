#!/usr/bin/env python3
"""Validate optional consumer-owned metadata without accepting policy authority."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "l9.ci-consumer/v1"
ALLOWED_KEYS = {"schema", "owner", "repo_class", "waiver_refs", "product_kind"}
ALLOWED_REPO_CLASSES = {"auto", "python", "typescript"}
# Quantum-L9/.github l9.product-kinds/global@1 admitted kinds. A repository
# declares what it is; Core decides which obligations that kind carries.
ALLOWED_PRODUCT_KINDS = {"node", "dependency"}
OWNER_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ConsumerMetadataError(RuntimeError):
    pass


def workspace_path(value: str) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    path = (workspace / value).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise ConsumerMetadataError(
            "consumer metadata path must remain inside GITHUB_WORKSPACE"
        ) from error
    return path


def emit(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def load_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ConsumerMetadataError(f"consumer metadata is not a file: {path}")
    if path.stat().st_size > 16384:
        raise ConsumerMetadataError("consumer metadata exceeds 16 KiB bound")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConsumerMetadataError(
            f"consumer metadata is not valid JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ConsumerMetadataError("consumer metadata must be an object")
    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        raise ConsumerMetadataError(
            f"consumer metadata contains forbidden keys: {unknown}"
        )
    if payload.get("schema") != SCHEMA:
        raise ConsumerMetadataError(f"consumer metadata schema must be {SCHEMA}")
    owner = payload.get("owner", "")
    if owner and (not isinstance(owner, str) or not OWNER_RE.fullmatch(owner)):
        raise ConsumerMetadataError(
            "owner must be an org/team-style pointer such as Quantum-L9/platform"
        )
    repo_class = payload.get("repo_class", "auto")
    if repo_class not in ALLOWED_REPO_CLASSES:
        raise ConsumerMetadataError(
            f"repo_class must be one of {sorted(ALLOWED_REPO_CLASSES)}"
        )
    waiver_refs = payload.get("waiver_refs", [])
    if not isinstance(waiver_refs, list) or not all(
        isinstance(item, str) and item for item in waiver_refs
    ):
        raise ConsumerMetadataError("waiver_refs must be an array of non-empty strings")
    if len(waiver_refs) > 32 or len(set(waiver_refs)) != len(waiver_refs):
        raise ConsumerMetadataError(
            "waiver_refs must contain at most 32 unique identifiers"
        )
    product_kind = payload.get("product_kind", "")
    if "product_kind" in payload and (
        not isinstance(product_kind, str) or product_kind not in ALLOWED_PRODUCT_KINDS
    ):
        raise ConsumerMetadataError(
            f"product_kind must be one of {sorted(ALLOWED_PRODUCT_KINDS)}"
        )
    payload["owner"] = owner
    payload["repo_class"] = repo_class
    payload["waiver_refs"] = sorted(waiver_refs)
    payload["product_kind"] = product_kind
    return payload


def main() -> int:
    try:
        path = workspace_path(os.environ.get("L9_CONSUMER_METADATA", ".l9/ci.json"))
        payload = load_metadata(path)
        if payload is None:
            emit("present", "false")
            emit("owner", "")
            emit("repo-class", "auto")
            emit("waiver-refs", "")
            emit("product-kind", "")
            return 0
        emit("present", "true")
        emit("owner", payload["owner"])
        emit("repo-class", payload["repo_class"])
        emit("waiver-refs", ",".join(payload["waiver_refs"]))
        emit("product-kind", payload["product_kind"])
        return 0
    except ConsumerMetadataError as error:
        print(f"resolve-consumer-metadata: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
