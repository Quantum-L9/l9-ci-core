#!/usr/bin/env python3
"""Preflight publication envelopes and publish one retry-safe check run."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class CheckPublicationError(RuntimeError):
    pass


# GitHub's check-run API accepts at most 50 annotations per request.
MAX_ANNOTATIONS = 50
MAX_CHECK_PAGES = 10
MAX_NAME = 255
MAX_SUMMARY = 60_000
MAX_TEXT = 60_000
MAX_PATH = 4096
MAX_MESSAGE = 64_000
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
MATRIX_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ALLOWED_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "neutral",
    "skipped",
    "stale",
    "success",
    "timed_out",
}


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CheckPublicationError(f"{name} is required")
    return value


def workspace_file(value: str, *, label: str) -> Path:
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
        raise CheckPublicationError(f"{label} path escapes GITHUB_WORKSPACE") from error
    if not path.is_file():
        raise CheckPublicationError(f"{label} file does not exist: {path}")
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
    if not document["name"].strip() or len(document["name"]) > MAX_NAME:
        raise CheckPublicationError("publication name must contain 1-255 characters")
    if not FULL_SHA.fullmatch(document["head_sha"]):
        raise CheckPublicationError("publication head_sha must be a full commit SHA")
    if document["status"] != "completed":
        raise CheckPublicationError("publication status must be completed")
    if document["conclusion"] not in ALLOWED_CONCLUSIONS:
        raise CheckPublicationError("publication conclusion is unsupported")
    output = document.get("output")
    if not isinstance(output, dict):
        raise CheckPublicationError("publication output must be an object")
    for field in ("title", "summary", "text"):
        if not isinstance(output.get(field), str):
            raise CheckPublicationError(
                f"publication output field {field!r} must be a string"
            )
    if not output["title"].strip() or len(output["title"]) > MAX_NAME:
        raise CheckPublicationError(
            "publication output title must contain 1-255 characters"
        )
    if len(output["summary"]) > MAX_SUMMARY or len(output["text"]) > MAX_TEXT:
        raise CheckPublicationError("publication output exceeds its text limit")
    annotations = output.get("annotations", [])
    if not isinstance(annotations, list):
        raise CheckPublicationError("publication annotations must be an array")
    if len(annotations) > MAX_ANNOTATIONS:
        raise CheckPublicationError(
            "publication exceeds the per-request annotation limit"
        )
    for index, annotation in enumerate(annotations):
        validate_annotation(annotation, index)
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise CheckPublicationError("publication metadata must be an object")
    run_url = metadata.get("run_url")
    parsed_url = urllib.parse.urlparse(run_url) if isinstance(run_url, str) else None
    if parsed_url is None or parsed_url.scheme != "https" or not parsed_url.netloc:
        raise CheckPublicationError("publication run_url must be an https URL")
    return document


def validate_annotation(annotation: Any, index: int) -> None:
    if not isinstance(annotation, dict):
        raise CheckPublicationError(f"publication annotation {index} must be an object")
    path = annotation.get("path")
    message = annotation.get("message")
    start_line = annotation.get("start_line")
    end_line = annotation.get("end_line")
    level = annotation.get("annotation_level")
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        raise CheckPublicationError(f"publication annotation {index} has invalid path")
    if path.startswith("/") or "\x00" in path:
        raise CheckPublicationError(
            f"publication annotation {index} path must be repository-relative"
        )
    if not isinstance(message, str) or not message or len(message) > MAX_MESSAGE:
        raise CheckPublicationError(
            f"publication annotation {index} has invalid message"
        )
    if not isinstance(start_line, int) or start_line < 1:
        raise CheckPublicationError(
            f"publication annotation {index} has invalid start_line"
        )
    if not isinstance(end_line, int) or end_line < start_line:
        raise CheckPublicationError(
            f"publication annotation {index} has invalid end_line"
        )
    if level not in {"notice", "warning", "failure"}:
        raise CheckPublicationError(
            f"publication annotation {index} has invalid annotation_level"
        )
    for field in ("title", "raw_details"):
        value = annotation.get(field)
        if value is not None and not isinstance(value, str):
            raise CheckPublicationError(
                f"publication annotation {index} field {field!r} must be a string"
            )


def validate_sarif_envelope(document: Any) -> dict[str, Any]:
    """Validate only the SDK projection's transport envelope, never its findings."""
    if not isinstance(document, dict):
        raise CheckPublicationError("SARIF document must be an object")
    if document.get("version") != "2.1.0":
        raise CheckPublicationError("unsupported SARIF document version")
    runs = document.get("runs")
    if not isinstance(runs, list) or not runs:
        raise CheckPublicationError("SARIF runs must be a non-empty array")
    schema = document.get("$schema")
    if schema is not None and not isinstance(schema, str):
        raise CheckPublicationError("SARIF $schema must be a string when present")
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise CheckPublicationError(f"SARIF run {index} must be an object")
        tool = run.get("tool")
        driver = tool.get("driver") if isinstance(tool, dict) else None
        driver_name = driver.get("name") if isinstance(driver, dict) else None
        if not isinstance(driver_name, str) or not driver_name.strip():
            raise CheckPublicationError(
                f"SARIF run {index} must identify tool.driver.name"
            )
    return document


def load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CheckPublicationError(f"{label} is invalid JSON: {error}") from error


def external_identity(run_id: str, matrix_id: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise CheckPublicationError("run id must be a positive integer")
    if not MATRIX_ID.fullmatch(matrix_id):
        raise CheckPublicationError("matrix id is invalid")
    # GITHUB_RUN_ID survives a workflow rerun while GITHUB_RUN_ATTEMPT changes.
    # The matrix id partitions independent publications inside the same run.
    return f"l9.core-publication/v1:{run_id}:{matrix_id}"


def preflight(publication: str, sarif: str) -> dict[str, Any]:
    path = workspace_file(publication, label="publication")
    document = validate_document(load_json(path, label="publication document"))
    if sarif:
        sarif_path = workspace_file(sarif, label="SARIF")
        validate_sarif_envelope(load_json(sarif_path, label="SARIF document"))
    return document


def preflight_sarif(sarif: str) -> None:
    sarif_path = workspace_file(sarif, label="SARIF")
    validate_sarif_envelope(load_json(sarif_path, label="SARIF document"))


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def api_request(
    token: str,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        ),
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Quantum-L9-l9-ci-core",
            "Content-Type": "application/json",
        },
    )
    # Defense-in-depth: urllib honors file://, so refuse anything but the
    # fixed HTTPS GitHub API endpoint before opening the request.
    if not request.full_url.startswith("https://api.github.com/"):
        raise CheckPublicationError(
            f"refusing non-GitHub Checks API URL: {request.full_url!r}"
        )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- fixed GitHub API origin enforced above
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        raise CheckPublicationError(
            f"GitHub Checks API returned HTTP {error.code}: {response_body}"
        ) from error
    except urllib.error.URLError as error:
        raise CheckPublicationError(
            f"GitHub Checks API request failed: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise CheckPublicationError(
            f"GitHub Checks API returned invalid JSON: {error}"
        ) from error
    if not isinstance(result, dict):
        raise CheckPublicationError("GitHub Checks API response must be an object")
    return result


def find_existing_check(
    token: str,
    repository: str,
    head_sha: str,
    identity: str,
) -> int | None:
    query_base = urllib.parse.urlencode({"filter": "all", "per_page": 100})
    for page in range(1, MAX_CHECK_PAGES + 1):
        result = api_request(
            token,
            "GET",
            f"https://api.github.com/repos/{repository}/commits/{head_sha}/check-runs"
            f"?{query_base}&page={page}",
        )
        check_runs = result.get("check_runs")
        if not isinstance(check_runs, list):
            raise CheckPublicationError(
                "GitHub response did not contain a check_runs array"
            )
        matches = [
            check
            for check in check_runs
            if isinstance(check, dict)
            and check.get("external_id") == identity
            and isinstance(check.get("id"), int)
        ]
        if matches:
            return max(check["id"] for check in matches)
        if len(check_runs) < 100:
            return None
    raise CheckPublicationError("GitHub check-run lookup exceeded pagination limit")


def publish_check(
    token: str,
    repository: str,
    document: dict[str, Any],
    identity: str,
) -> tuple[dict[str, Any], str]:
    create_body = {
        "name": document["name"],
        "head_sha": document["head_sha"],
        "status": document["status"],
        "conclusion": document["conclusion"],
        "details_url": document["metadata"]["run_url"],
        "external_id": identity,
        "output": document["output"],
    }
    existing_id = find_existing_check(
        token,
        repository,
        document["head_sha"],
        identity,
    )
    if existing_id is None:
        result = api_request(
            token,
            "POST",
            f"https://api.github.com/repos/{repository}/check-runs",
            create_body,
        )
        return result, "created"
    update_body = {
        key: value for key, value in create_body.items() if key != "head_sha"
    }
    result = api_request(
        token,
        "PATCH",
        f"https://api.github.com/repos/{repository}/check-runs/{existing_id}",
        update_body,
    )
    return result, "updated"


def main() -> int:
    try:
        repository = required("L9_REPOSITORY")
        if not REPOSITORY.fullmatch(repository):
            raise CheckPublicationError("repository must have owner/name form")
        sarif = os.environ.get("L9_SARIF", "").strip()
        if os.environ.get("L9_PREFLIGHT_SARIF_ONLY", "false") == "true":
            preflight_sarif(required("L9_SARIF"))
            return 0
        identity = external_identity(
            required("L9_RUN_ID"),
            required("L9_MATRIX_ID"),
        )
        document = preflight(
            required("L9_PUBLICATION"),
            sarif,
        )
        emit("external-id", identity)
        if os.environ.get("L9_PREFLIGHT_ONLY", "false") == "true":
            return 0
        token = required("GITHUB_TOKEN")
        result, operation = publish_check(token, repository, document, identity)
        check_id = result.get("id")
        check_url = result.get("html_url")
        if not isinstance(check_id, int):
            raise CheckPublicationError(
                "GitHub response did not contain a check-run id"
            )
        emit("check-run-id", str(check_id))
        emit("check-run-operation", operation)
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
