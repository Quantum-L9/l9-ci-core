"""The release identity document: ``l9-release.json``.

A release already carries bytes (the assets), their digests (``SHA256SUMS``)
and a provenance block in the release notes. None of that is machine-readable
as one object, so every consumer that wants to pin a release has to assemble
the identity itself from the tag, the API and the checksum file. This module
emits that identity as a single asset so a consumer can answer "which version,
which commit, which channel, which digest" with one fetch.

The document is derived, never authored: it is built from the same
``SHA256SUMS`` the release publishes, so its ``files`` map cannot disagree with
the assets. It is deliberately **not** listed in ``SHA256SUMS`` (a file cannot
name its own digest without a fixed point); instead ``verify`` treats it as the
one known extra asset and checks every field against the release it describes.
An attacker who rewrites the document cannot change the assets, and one who
rewrites an asset is caught by ``SHA256SUMS``.

``published_at`` is the release commit's epoch, not wall-clock time, so two
builds of the same commit produce a byte-identical document.
"""

from __future__ import annotations

import datetime
import json

from .errors import ReleaseRefused

SCHEMA = "l9.release-identity/v1"
NAME = "l9-release.json"

_FIELDS = (
    "schema",
    "repository",
    "version",
    "tag",
    "channel",
    "commit",
    "adapter",
    "source_date_epoch",
    "published_at",
    "files",
)


def identity_document(
    repository: str,
    version: str,
    tag: str,
    channel: str,
    commit: str,
    adapter: str,
    sums: dict[str, str],
    source_date_epoch: int,
) -> dict[str, object]:
    """Build the document from values the release already proves."""
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ReleaseRefused(f"identity document needs a full commit, not {commit!r}")
    if not sums:
        raise ReleaseRefused("identity document needs at least one artifact digest")
    published = datetime.datetime.fromtimestamp(
        source_date_epoch, tz=datetime.UTC
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": SCHEMA,
        "repository": repository,
        "version": version,
        "tag": tag,
        "channel": channel,
        "commit": commit,
        "adapter": adapter,
        "source_date_epoch": source_date_epoch,
        "published_at": published,
        "files": dict(sorted(sums.items())),
    }


def encode(document: dict[str, object]) -> bytes:
    """Stable bytes: fixed key order, two-space indent, one trailing newline."""
    ordered = {name: document[name] for name in _FIELDS if name in document}
    unknown = sorted(set(document) - set(_FIELDS))
    if unknown:
        raise ReleaseRefused(f"identity document holds unknown fields {unknown}")
    return (json.dumps(ordered, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def validate(
    document: object,
    *,
    repository: str,
    tag: str,
    version: str,
    commit: str,
    channel: str,
    sums: dict[str, str],
) -> None:
    """Prove a published document describes the release it is attached to."""
    if not isinstance(document, dict):
        raise ReleaseRefused(f"{NAME} is not a JSON object")
    missing = sorted(set(_FIELDS) - set(document))
    unknown = sorted(set(document) - set(_FIELDS))
    if missing or unknown:
        raise ReleaseRefused(
            f"{NAME} fields disagree with the schema: missing {missing}, "
            f"unknown {unknown}"
        )
    expected: dict[str, object] = {
        "schema": SCHEMA,
        "repository": repository,
        "version": version,
        "tag": tag,
        "commit": commit,
        "channel": channel,
    }
    for name, value in expected.items():
        if document[name] != value:
            raise ReleaseRefused(f"{NAME} {name} is {document[name]!r}, not {value!r}")
    files = document["files"]
    if not isinstance(files, dict) or not files:
        raise ReleaseRefused(f"{NAME} names no artifact")
    if set(files) != set(sums):
        raise ReleaseRefused(
            f"{NAME} names {sorted(files)}, but {len(sums)} asset(s) are published"
        )
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ReleaseRefused(f"{NAME} file entry {name!r} is not a string pair")
        if sums[name] != digest:
            raise ReleaseRefused(
                f"{NAME} digest for {name} is {digest}, but SHA256SUMS says "
                f"{sums[name]}"
            )
