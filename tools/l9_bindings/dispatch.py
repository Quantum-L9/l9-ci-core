"""The one emitter of ``l9.release.requested.v2``.

A ``deployed`` binding names an environment — ``c1`` for production,
``c1-staging`` for pre-production — and a producer channel. It owns no file to
rewrite and no lock to move, so converging it means asking ``l9-deploy`` to
deploy the release: a ``repository_dispatch`` of type
``l9.release.requested.v2`` whose ``client_payload`` is an
``l9.deployment-request/v2`` document.

That document is **not** assembled here. It names the image digest, the
deployment-profile digest and a ``l9.release-evidence-reference/v1`` whose
gate/artifact binding digests point at files only the producer's build wrote.
A tag cannot prove any of it, and a consumer that invented those values would
be asserting an artifact it never built. So the producer publishes the request
with the release (``deployment-request.json``, beside ``l9-release.json``), and
this module proves the published document is about the release the binding
resolved to before it dispatches anything.

What the consumer owns, and therefore what is re-bound here:

* ``target.environment`` — the binding's environment, which may differ from the
  one the producer's own release workflow asked for (a release built for
  staging can be promoted to ``c1`` by a binding that says so);
* ``request_id`` — derived from the idempotency key, so a retry of the same
  emission is the same request, not a second deployment;
* ``idempotency_key`` — ``release:<producer>:<commit>:<environment>``, the same
  shape ``container-release`` emits, so l9-deploy dedupes a producer dispatch
  and a binding dispatch of the same release;
* ``requested_by`` — the consumer and the binding that asked;
* ``requested_at`` — when the dispatch was made.

Everything else is copied byte for byte from the producer's document. Fail
closed: no published request, a request about another repository or another
commit, a field outside the contract, or a failed dispatch is an error the
caller turns into a ``blocked`` verdict — never a guessed payload.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from typing import Any

from .model import Binding

API = "https://api.github.com"
#: The repository that executes a request. It is the only consumer of the
#: event, so it is a constant of the contract, not a per-binding choice.
DEPLOY_REPOSITORY = "Quantum-L9/l9-deploy"
EVENT = "l9.release.requested.v2"
REQUEST_SCHEMA = "l9.deployment-request/v2"
EVIDENCE_SCHEMA = "l9.release-evidence-reference/v1"
#: The release asset a producer publishes beside ``l9-release.json``.
REQUEST_ASSET = "deployment-request.json"
PROFILE_PATH = ".l9/deployment.yaml"

_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_IMAGE = re.compile(r"^ghcr\.io/[a-z0-9._-]+/[a-z0-9._/-]+$")
_IMAGE_REF = re.compile(r"^ghcr\.io/[a-z0-9._-]+/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ENVIRONMENT = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_ARCHITECTURES = ("linux/amd64", "linux/arm64")

_REQUEST_KEYS = frozenset(
    {
        "schema",
        "request_id",
        "idempotency_key",
        "source",
        "artifact",
        "profile",
        "evidence",
        "target",
        "requested_by",
        "requested_at",
    }
)
_SOURCE_KEYS = frozenset({"repository", "commit_sha", "ref", "run_id"})
_ARTIFACT_KEYS = frozenset({"image", "digest", "image_ref", "architecture"})
_PROFILE_KEYS = frozenset({"path", "digest"})
_TARGET_KEYS = frozenset({"environment"})
_EVIDENCE_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "sdk_version",
        "bundle_digest",
        "workflow_run_id",
        "artifact_name",
        "bundle_path",
        "provenance_reference",
        "sbom_reference",
        "gate_binding_digest",
        "gate_binding_path",
        "artifact_binding_digest",
        "artifact_binding_path",
    }
)
_EVIDENCE_DIGESTS = (
    "bundle_digest",
    "gate_binding_digest",
    "artifact_binding_digest",
)
_EVIDENCE_TEXT = (
    "schema_version",
    "sdk_version",
    "artifact_name",
    "bundle_path",
    "provenance_reference",
    "sbom_reference",
    "gate_binding_path",
    "artifact_binding_path",
)


class DispatchError(Exception):
    """The request cannot be proved or the dispatch failed; nothing was sent."""


def _fail(where: str, message: str) -> DispatchError:
    return DispatchError(f"{where}: {message}")


def _mapping(
    value: object, where: str, keys: frozenset[str], required: frozenset[str]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, "must be an object")
    unknown = sorted(set(value) - keys)
    if unknown:
        raise _fail(where, f"names unknown field(s) {unknown}")
    missing = sorted(required - set(value))
    if missing:
        raise _fail(where, f"is missing field(s) {missing}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, "must be a non-empty string")
    return value


def _pattern(value: object, where: str, pattern: re.Pattern[str], what: str) -> str:
    text = _text(value, where)
    if not pattern.match(text):
        raise _fail(where, f"must be {what}: {text!r}")
    return text


def _integer(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _fail(where, "must be a positive integer")
    return value


def _timestamp(value: object, where: str) -> str:
    text = _text(value, where)
    try:
        datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise _fail(where, f"must be an ISO-8601 timestamp: {text!r}") from error
    return text


def validate(document: object, *, producer: str, commit: str) -> dict[str, Any]:
    """Prove ``document`` is the request the contract defines for this release.

    Returns the document as a dict. Every check is structural and local; the
    release identity it must agree with comes from the caller, which resolved
    it from the tag.
    """

    request = _mapping(document, "deployment request", _REQUEST_KEYS, _REQUEST_KEYS)
    if request["schema"] != REQUEST_SCHEMA:
        raise _fail(
            "deployment request",
            f"schema must be {REQUEST_SCHEMA!r}, got {request['schema']!r}",
        )
    try:
        uuid.UUID(_text(request["request_id"], "deployment request.request_id"))
    except ValueError as error:
        raise _fail("deployment request.request_id", "must be a UUID") from error
    key = _text(request["idempotency_key"], "deployment request.idempotency_key")
    if not 16 <= len(key) <= 200:
        raise _fail(
            "deployment request.idempotency_key",
            "must be 16 to 200 characters",
        )

    source = _mapping(
        request["source"], "deployment request.source", _SOURCE_KEYS, _SOURCE_KEYS
    )
    repository = _pattern(
        source["repository"],
        "deployment request.source.repository",
        _REPOSITORY,
        "owner/name",
    )
    if repository.lower() != producer.lower():
        raise _fail(
            "deployment request.source.repository",
            f"names {repository!r}, but the binding's producer is {producer!r}",
        )
    declared_commit = _pattern(
        source["commit_sha"],
        "deployment request.source.commit_sha",
        _COMMIT,
        "a full lowercase commit SHA",
    )
    if declared_commit != commit:
        raise _fail(
            "deployment request.source.commit_sha",
            f"is {declared_commit[:8]}, but the release is {commit[:8]}",
        )
    ref = _text(source["ref"], "deployment request.source.ref")
    if not ref.startswith("refs/"):
        raise _fail("deployment request.source.ref", f"must start with refs/: {ref!r}")
    _integer(source["run_id"], "deployment request.source.run_id")

    artifact = _mapping(
        request["artifact"],
        "deployment request.artifact",
        _ARTIFACT_KEYS,
        _ARTIFACT_KEYS - {"architecture"},
    )
    image = _pattern(
        artifact["image"],
        "deployment request.artifact.image",
        _IMAGE,
        "a ghcr.io image without a tag or digest",
    )
    digest = _pattern(
        artifact["digest"],
        "deployment request.artifact.digest",
        _DIGEST,
        "sha256:<64 hex>",
    )
    image_ref = _pattern(
        artifact["image_ref"],
        "deployment request.artifact.image_ref",
        _IMAGE_REF,
        "a ghcr.io image pinned by digest",
    )
    if image_ref != f"{image}@{digest}":
        raise _fail(
            "deployment request.artifact.image_ref",
            f"must be {image}@{digest}",
        )
    if "architecture" in artifact and artifact["architecture"] not in _ARCHITECTURES:
        raise _fail(
            "deployment request.artifact.architecture",
            f"must be one of {list(_ARCHITECTURES)}",
        )

    profile = _mapping(
        request["profile"], "deployment request.profile", _PROFILE_KEYS, _PROFILE_KEYS
    )
    if profile["path"] != PROFILE_PATH:
        raise _fail(
            "deployment request.profile.path",
            f"must be {PROFILE_PATH!r}, got {profile['path']!r}",
        )
    _pattern(
        profile["digest"],
        "deployment request.profile.digest",
        _DIGEST,
        "sha256:<64 hex>",
    )

    evidence = _mapping(
        request["evidence"],
        "deployment request.evidence",
        _EVIDENCE_KEYS,
        _EVIDENCE_KEYS,
    )
    if evidence["schema"] != EVIDENCE_SCHEMA:
        raise _fail(
            "deployment request.evidence.schema",
            f"must be {EVIDENCE_SCHEMA!r}, got {evidence['schema']!r}",
        )
    for name in _EVIDENCE_DIGESTS:
        _pattern(
            evidence[name],
            f"deployment request.evidence.{name}",
            _DIGEST,
            "sha256:<64 hex>",
        )
    for name in _EVIDENCE_TEXT:
        _text(evidence[name], f"deployment request.evidence.{name}")
    _integer(evidence["workflow_run_id"], "deployment request.evidence.workflow_run_id")

    target = _mapping(
        request["target"], "deployment request.target", _TARGET_KEYS, _TARGET_KEYS
    )
    _pattern(
        target["environment"],
        "deployment request.target.environment",
        _ENVIRONMENT,
        "a lowercase environment name (c1, c1-staging)",
    )
    _text(request["requested_by"], "deployment request.requested_by")
    _timestamp(request["requested_at"], "deployment request.requested_at")
    return request


def idempotency_key(producer: str, commit: str, environment: str) -> str:
    """The key l9-deploy dedupes on: one deployment per release and target."""
    return f"release:{producer}:{commit}:{environment}"


def bind(
    document: Mapping[str, Any],
    *,
    consumer: str,
    binding: Binding,
    producer: str,
    commit: str,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Re-bind a proved request to the consumer's binding and environment.

    The request identity is derived, not random: the same producer, commit and
    environment always produce the same ``request_id`` and ``idempotency_key``,
    so a re-run of ``converge`` is the same deployment, not a second one.
    """

    environment = binding.environment
    assert environment is not None
    payload = json.loads(json.dumps(document))
    key = idempotency_key(producer, commit, environment)
    payload["idempotency_key"] = key
    payload["request_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
    payload["target"] = {"environment": environment}
    payload["requested_by"] = f"{consumer} ({binding.id})"
    payload["requested_at"] = (
        (now or datetime.datetime.now(datetime.UTC))
        .astimezone(datetime.UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return payload


def _post(url: str, token: str, body: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST"
    )
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30):
            return
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        raise DispatchError(
            f"POST {url} failed with HTTP {error.code}: {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise DispatchError(f"POST {url} failed: {error.reason}") from error


def dispatch(
    payload: Mapping[str, Any],
    token: str,
    *,
    repository: str = DEPLOY_REPOSITORY,
) -> None:
    """Send the event. Raises ``DispatchError``; returns only on success."""
    if not token:
        raise DispatchError("dispatching a deployment request needs a token")
    _post(
        f"{API}/repos/{repository}/dispatches",
        token,
        {"event_type": EVENT, "client_payload": dict(payload)},
    )


def emit(
    document: Mapping[str, Any],
    *,
    consumer: str,
    binding: Binding,
    producer: str,
    commit: str,
    token: str,
    repository: str = DEPLOY_REPOSITORY,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Prove, re-bind and dispatch one request; return what was sent."""

    validate(document, producer=producer, commit=commit)
    payload = bind(
        document,
        consumer=consumer,
        binding=binding,
        producer=producer,
        commit=commit,
        now=now,
    )
    # Prove what is about to be sent, not only what was read: a binding whose
    # environment or identity could not survive the contract must fail here,
    # before l9-deploy ever sees the event.
    validate(payload, producer=producer, commit=commit)
    dispatch(payload, token, repository=repository)
    return payload
