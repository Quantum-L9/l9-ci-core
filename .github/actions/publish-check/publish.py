#!/usr/bin/env python3
"""Publish a completed check run with bounded annotations."""

from __future__ import annotations
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class CheckPublicationError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CheckPublicationError(f"{name} is required")
    return value


def publication_path(value: str) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    candidate = Path(value)
    path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace / candidate).resolve()
    )
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise CheckPublicationError(
            "publication path escapes GITHUB_WORKSPACE"
        ) from error
    if not path.is_file():
        raise CheckPublicationError(f"publication file does not exist: {path}")
    return path


def validate_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise CheckPublicationError("publication document must be an object")
    if document.get("schema") != "l9.core-publication/v1":
        raise CheckPublicationError("unsupported publication document schema")
    required_strings = (
        "name",
        "head_sha",
        "status",
        "conclusion",
    )
    for field in required_strings:
        if not isinstance(document.get(field), str):
            raise CheckPublicationError(f"publication field {field!r} must be a string")
    output = document.get("output")
    if not isinstance(output, dict):
        raise CheckPublicationError("publication output must be an object")
    annotations = output.get("annotations", [])
    if not isinstance(annotations, list):
        raise CheckPublicationError("publication annotations must be an array")
    if len(annotations) > 50:
        raise CheckPublicationError(
            "publication exceeds the per-request annotation limit"
        )
    return document


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    try:
        token = required("GITHUB_TOKEN")
        repository = required("L9_REPOSITORY")
        path = publication_path(required("L9_PUBLICATION"))
        if repository.count("/") != 1:
            raise CheckPublicationError("repository must have owner/name form")
        try:
            document = validate_document(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as error:
            raise CheckPublicationError(
                f"publication document is invalid JSON: {error}"
            ) from error
        request_body = {
            "name": document["name"],
            "head_sha": document["head_sha"],
            "status": document["status"],
            "conclusion": document["conclusion"],
            "details_url": document["metadata"]["run_url"],
            "output": document["output"],
        }
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/check-runs",
            data=json.dumps(request_body).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Quantum-L9-l9-ci-core",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise CheckPublicationError(
                f"GitHub Checks API returned HTTP {error.code}: {body}"
            ) from error
        except urllib.error.URLError as error:
            raise CheckPublicationError(
                f"GitHub Checks API request failed: {error}"
            ) from error
        check_id = result.get("id")
        check_url = result.get("html_url")
        if not isinstance(check_id, int):
            raise CheckPublicationError(
                "GitHub response did not contain a check-run id"
            )
        emit("check-run-id", str(check_id))
        emit(
            "check-run-url",
            check_url if isinstance(check_url, str) else "",
        )
        return 0
    except CheckPublicationError as error:
        print(f"publish-check: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
