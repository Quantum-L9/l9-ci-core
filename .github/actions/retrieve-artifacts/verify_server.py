#!/usr/bin/env python3
"""Verify descriptor-bound GitHub artifact metadata before immutable-ID download."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
POSITIVE_ID = re.compile(r"^[1-9][0-9]{0,19}$")
SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_RESPONSE_BYTES = 1024 * 1024


class ServerVerificationError(RuntimeError):
    """GitHub does not confirm the descriptor's immutable artifact identity."""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ServerVerificationError(f"{name} is required")
    return value


def checked(name: str, pattern: re.Pattern[str]) -> str:
    value = required(name)
    if not pattern.fullmatch(value):
        raise ServerVerificationError(f"{name} has an invalid value")
    return value


def parse_json(content: bytes) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    try:
        document = json.loads(content, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ServerVerificationError(
            f"artifact metadata is invalid JSON: {error}"
        ) from error
    if not isinstance(document, dict):
        raise ServerVerificationError("artifact metadata must be a JSON object")
    return document


def parse_time(value: Any, *, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ServerVerificationError(f"artifact metadata {field} is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        instant = dt.datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ServerVerificationError(
            f"artifact metadata {field} is not an RFC 3339 timestamp"
        ) from error
    if instant.tzinfo is None:
        raise ServerVerificationError(f"artifact metadata {field} has no timezone")
    return instant.astimezone(dt.timezone.utc)


def normalize_digest(value: Any) -> str:
    if not isinstance(value, str):
        raise ServerVerificationError("artifact metadata digest is missing")
    normalized = value.lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.removeprefix("sha256:")
    if not SHA256.fullmatch(normalized):
        raise ServerVerificationError("artifact metadata digest is not SHA-256")
    return normalized


def trusted_https(url: str, *, reason: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ServerVerificationError(reason)


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A metadata GET is one HTTPS response. Never follow a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ServerVerificationError(
            "GitHub artifact metadata request refused a redirect"
        )


class _RefuseNonHttps(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request):
        raise ServerVerificationError("artifact metadata URL is not HTTPS")


class _RefuseFile(urllib.request.FileHandler):
    def file_open(self, req: urllib.request.Request):
        raise ServerVerificationError("artifact metadata URL is not HTTPS")


class _RefuseFTP(urllib.request.FTPHandler):
    def ftp_open(self, req: urllib.request.Request):
        raise ServerVerificationError("artifact metadata URL is not HTTPS")


class _RefuseData(urllib.request.DataHandler):
    def data_open(self, req: urllib.request.Request):
        raise ServerVerificationError("artifact metadata URL is not HTTPS")


def _https_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        _RefuseRedirect,
        _RefuseNonHttps,
        _RefuseFile,
        _RefuseFTP,
        _RefuseData,
    )


def metadata_url(api_url: str, repository: str, artifact_id: str) -> str:
    trusted_https(api_url, reason="GITHUB_API_URL is not a trusted HTTPS base URL")
    base = api_url.rstrip("/")
    owner, name = repository.split("/", 1)
    return (
        f"{base}/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/actions/artifacts/{artifact_id}"
    )


def fetch_metadata(url: str, token: str) -> dict[str, Any]:
    trusted_https(url, reason="artifact metadata URL is not a trusted HTTPS URL")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "l9-ci-core-retrieve-artifacts",
        },
        method="GET",
    )
    try:
        with _https_opener().open(request, timeout=30) as response:
            final = response.geturl()
            if final != url:
                raise ServerVerificationError(
                    "GitHub artifact metadata request refused a redirect"
                )
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except ServerVerificationError:
        raise
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as error:
        raise ServerVerificationError(
            f"GitHub artifact metadata request failed: {error}"
        ) from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise ServerVerificationError("GitHub artifact metadata response is too large")
    return parse_json(content)


def verify_metadata(
    document: dict[str, Any],
    *,
    metadata_endpoint: str | None = None,
    now: dt.datetime | None = None,
) -> None:
    artifact_id = int(checked("L9_ARTIFACT_ID", POSITIVE_ID))
    run_id = int(checked("L9_SOURCE_RUN_ID", POSITIVE_ID))
    artifact_name = checked("L9_ARTIFACT_NAME", SAFE_ARTIFACT_NAME)
    expected_digest = checked("L9_ARCHIVE_DIGEST", SHA256)
    workflow_head_sha = checked("L9_WORKFLOW_HEAD_SHA", FULL_SHA)

    server_id = document.get("id")
    if isinstance(server_id, bool) or not isinstance(server_id, int):
        raise ServerVerificationError("artifact metadata id is invalid")
    if server_id != artifact_id:
        raise ServerVerificationError("artifact metadata id does not match descriptor")
    if document.get("name") != artifact_name:
        raise ServerVerificationError(
            "artifact metadata name does not match descriptor"
        )
    if metadata_endpoint is not None:
        if document.get("url") != metadata_endpoint:
            raise ServerVerificationError(
                "artifact metadata URL does not match the descriptor repository and ID"
            )
        if document.get("archive_download_url") != f"{metadata_endpoint}/zip":
            raise ServerVerificationError(
                "artifact archive URL does not match the descriptor repository and ID"
            )
    if document.get("expired") is not False:
        raise ServerVerificationError(
            "artifact is expired or has unknown lifecycle state"
        )
    if normalize_digest(document.get("digest")) != expected_digest:
        raise ServerVerificationError(
            "artifact metadata archive digest does not match descriptor"
        )

    workflow_run = document.get("workflow_run")
    if not isinstance(workflow_run, dict):
        raise ServerVerificationError("artifact metadata workflow_run is missing")
    server_run_id = workflow_run.get("id")
    if isinstance(server_run_id, bool) or not isinstance(server_run_id, int):
        raise ServerVerificationError("artifact metadata workflow_run id is invalid")
    if server_run_id != run_id:
        raise ServerVerificationError(
            "artifact metadata workflow run does not match descriptor"
        )
    if workflow_run.get("head_sha") != workflow_head_sha:
        raise ServerVerificationError(
            "artifact metadata workflow revision does not match descriptor"
        )

    created = parse_time(document.get("created_at"), field="created_at")
    updated = parse_time(document.get("updated_at"), field="updated_at")
    expiration = parse_time(document.get("expires_at"), field="expires_at")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    if not created <= updated <= expiration:
        raise ServerVerificationError("artifact lifecycle timestamps are inconsistent")
    if created > current:
        raise ServerVerificationError("artifact creation time is in the future")
    if expiration <= current:
        raise ServerVerificationError("artifact has reached its expiration time")


def main() -> int:
    try:
        repository = checked("L9_SOURCE_REPOSITORY", REPOSITORY)
        artifact_id = checked("L9_ARTIFACT_ID", POSITIVE_ID)
        token = required("L9_TOKEN")
        url = metadata_url(required("GITHUB_API_URL"), repository, artifact_id)
        verify_metadata(fetch_metadata(url, token), metadata_endpoint=url)
        return 0
    except (OSError, ServerVerificationError) as error:
        print(f"retrieve-artifacts: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
