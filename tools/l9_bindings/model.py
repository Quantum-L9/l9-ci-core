"""``l9.bindings/v1``: the consumer edge, declared.

A binding is one consumer's statement that it depends on one producer's
release channel, where the resolved identity is written, and what must happen
before that statement may change. The schema is closed: an unknown key fails,
because a binding that silently ignores a policy key is worse than no binding.

The file lives in the consumer (``.l9/bindings.yaml``) and is the only input
the converge engine reads. Nothing is inferred from a lock file, a workflow or
a human's memory, so the set of edges in the organization is exactly the set of
declared bindings.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

SCHEMA = "l9.bindings/v1"
PATH = pathlib.Path(".l9/bindings.yaml")

CLASSES = ("library", "deployed", "contract")
FIELD_NAMES = ("version", "revision", "digest", "revision_note")

_TOP_KEYS = {"schema", "consumer", "bindings"}
_BINDING_KEYS = {
    "id",
    "producer",
    "channel",
    "class",
    "description",
    "file",
    "fields",
    "lock",
    "package",
    "refresh",
    "artifact",
    "environment",
    "proof",
    "policy",
}
_POLICY_KEYS = {"auto_pr", "auto_merge", "requires_proof"}

_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CHANNEL = re.compile(r"^v[0-9]+$")
_DOTTED = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")


class BindingsInvalid(Exception):
    """The declared edge set cannot be trusted, so nothing may act on it."""


@dataclass(frozen=True)
class Field:
    """Every destination for one resolved identity value.

    A value frequently appears twice in a binding document — the version in a
    ``package_version`` field and again in a ``release_evidence`` block — and a
    rewrite that moves one and not the other leaves the document contradicting
    itself. The model therefore carries a tuple, and a drift check reads all of
    them.
    """

    paths: tuple[str, ...]
    template: str | None = None


@dataclass(frozen=True)
class Policy:
    auto_pr: bool
    auto_merge: bool
    requires_proof: bool


@dataclass(frozen=True)
class Binding:
    id: str
    producer: str
    channel: str
    klass: str
    policy: Policy
    description: str = ""
    file: str | None = None
    fields: dict[str, Field] = field(default_factory=dict)
    lock: str | None = None
    package: str | None = None
    refresh: str | None = None
    artifact: str | None = None
    environment: str | None = None
    proof: str | None = None

    @property
    def producer_url(self) -> str:
        return f"https://github.com/{self.producer}.git"


@dataclass(frozen=True)
class Bindings:
    consumer: str
    bindings: tuple[Binding, ...]

    def by_id(self, identifier: str) -> Binding:
        for binding in self.bindings:
            if binding.id == identifier:
                return binding
        raise BindingsInvalid(f"no binding {identifier!r} is declared")


def _fail(where: str, message: str) -> BindingsInvalid:
    return BindingsInvalid(f"{where}: {message}")


def _string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, "must be a non-empty string")
    return value.strip()


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(where, "must be true or false")
    return value


def _fields(raw: object, where: str) -> dict[str, Field]:
    if not isinstance(raw, dict) or not raw:
        raise _fail(where, "must be a non-empty mapping")
    unknown = sorted(set(raw) - set(FIELD_NAMES))
    if unknown:
        raise _fail(where, f"names unknown field(s) {unknown}")
    parsed: dict[str, Field] = {}
    for name, value in raw.items():
        at = f"{where}.{name}"
        if isinstance(value, (str, list)):
            parsed[name] = Field(paths=_paths(value, at))
            continue
        if not isinstance(value, dict):
            raise _fail(at, "must be a dotted path or a mapping with path")
        extra = sorted(set(value) - {"path", "template"})
        if extra:
            raise _fail(at, f"names unknown key(s) {extra}")
        paths = _paths(value.get("path"), f"{at}.path")
        template = value.get("template")
        if template is not None:
            template = _string(template, f"{at}.template")
            if name != "revision_note":
                raise _fail(at, "only revision_note accepts a template")
            known = {"tag", "commit", "object", "version", "channel"}
            used = {token for token in known if "{" + token + "}" in template}
            unknown_tokens = set(re.findall(r"\{([a-z_]+)\}", template)) - known
            if unknown_tokens:
                raise _fail(
                    at, f"template names unknown token(s) {sorted(unknown_tokens)}"
                )
            if not used:
                raise _fail(at, "template names no known token")
        parsed[name] = Field(paths=paths, template=template)
    return parsed


def _paths(raw: object, where: str) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (_string(raw, where),)
    if isinstance(raw, list) and raw:
        return tuple(
            _string(entry, f"{where}[{index}]") for index, entry in enumerate(raw)
        )
    raise _fail(where, "must be a dotted path or a non-empty list of them")


def _policy(raw: object, klass: str, where: str) -> Policy:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _fail(where, "must be a mapping")
    unknown = sorted(set(raw) - _POLICY_KEYS)
    if unknown:
        raise _fail(where, f"names unknown key(s) {unknown}")
    auto_pr = _boolean(raw.get("auto_pr", klass != "deployed"), f"{where}.auto_pr")
    auto_merge = _boolean(raw.get("auto_merge", False), f"{where}.auto_merge")
    requires_proof = _boolean(
        raw.get("requires_proof", klass == "contract"), f"{where}.requires_proof"
    )
    if klass == "contract" and not requires_proof:
        raise _fail(where, "a contract binding always requires its proof")
    if klass == "contract" and auto_merge:
        raise _fail(
            where,
            "a contract binding is never auto-merged: its pin carries prose "
            "invariants a field rewrite cannot regenerate",
        )
    if klass == "deployed" and auto_pr:
        raise _fail(where, "a deployed binding has no pull request; it dispatches")
    return Policy(auto_pr=auto_pr, auto_merge=auto_merge, requires_proof=requires_proof)


def _binding(raw: object, position: int) -> Binding:
    where = f"bindings[{position}]"
    if not isinstance(raw, dict):
        raise _fail(where, "must be a mapping")
    unknown = sorted(set(raw) - _BINDING_KEYS)
    if unknown:
        raise _fail(where, f"names unknown key(s) {unknown}")
    identifier = _string(raw.get("id"), f"{where}.id")
    if not _SLUG.match(identifier):
        raise _fail(f"{where}.id", "must match ^[a-z][a-z0-9-]*$")
    where = f"bindings[{identifier}]"
    producer = _string(raw.get("producer"), f"{where}.producer")
    if not _REPO.match(producer):
        raise _fail(f"{where}.producer", "must be owner/name")
    channel = _string(raw.get("channel"), f"{where}.channel")
    if not _CHANNEL.match(channel):
        raise _fail(f"{where}.channel", "must be a major channel such as v1")
    klass = _string(raw.get("class"), f"{where}.class")
    if klass not in CLASSES:
        raise _fail(f"{where}.class", f"must be one of {list(CLASSES)}")
    policy = _policy(raw.get("policy"), klass, f"{where}.policy")
    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise _fail(f"{where}.description", "must be a string")

    binding = Binding(
        id=identifier,
        producer=producer,
        channel=channel,
        klass=klass,
        policy=policy,
        description=description,
        file=_string(raw["file"], f"{where}.file") if "file" in raw else None,
        fields=_fields(raw["fields"], f"{where}.fields") if "fields" in raw else {},
        lock=_string(raw["lock"], f"{where}.lock") if "lock" in raw else None,
        package=_string(raw["package"], f"{where}.package")
        if "package" in raw
        else None,
        refresh=_string(raw["refresh"], f"{where}.refresh")
        if "refresh" in raw
        else None,
        artifact=_string(raw["artifact"], f"{where}.artifact")
        if "artifact" in raw
        else None,
        environment=_string(raw["environment"], f"{where}.environment")
        if "environment" in raw
        else None,
        proof=_string(raw["proof"], f"{where}.proof") if "proof" in raw else None,
    )
    _check_class(binding, where)
    return binding


def _check_class(binding: Binding, where: str) -> None:
    if binding.klass == "library":
        for name in ("lock", "package", "refresh"):
            if getattr(binding, name) is None:
                raise _fail(where, f"a library binding requires {name}")
        refresh = binding.refresh or ""
        for token in ("package", "version"):
            if "{" + token + "}" not in refresh:
                raise _fail(f"{where}.refresh", f"must name {{{token}}}")
        if binding.file or binding.fields or binding.environment:
            raise _fail(where, "a library binding names a lock, not a file or target")
    elif binding.klass == "contract":
        if not binding.file or not binding.fields or not binding.proof:
            raise _fail(where, "a contract binding requires file, fields and proof")
        if binding.lock or binding.environment:
            raise _fail(where, "a contract binding names a file, not a lock or target")
    else:
        if not binding.environment:
            raise _fail(where, "a deployed binding requires an environment")
        if binding.file or binding.fields or binding.lock:
            raise _fail(where, "a deployed binding dispatches; it writes no file")
    for name, entry in binding.fields.items():
        for path in entry.paths:
            if not _DOTTED.match(path):
                raise _fail(f"{where}.fields.{name}", f"{path!r} is not a dotted path")
    if "digest" in binding.fields and not binding.artifact:
        raise _fail(
            where,
            "a digest field must name which published file it pins (artifact:)",
        )


def parse(document: object, source: str = "<memory>") -> Bindings:
    if not isinstance(document, dict):
        raise _fail(source, "must be a mapping")
    unknown = sorted(set(document) - _TOP_KEYS)
    if unknown:
        raise _fail(source, f"names unknown key(s) {unknown}")
    if document.get("schema") != SCHEMA:
        raise _fail(source, f"schema must be {SCHEMA!r}")
    consumer = _string(document.get("consumer"), f"{source}.consumer")
    if not _REPO.match(consumer):
        raise _fail(f"{source}.consumer", "must be owner/name")
    raw_bindings = document.get("bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise _fail(source, "must declare at least one binding")
    bindings = tuple(
        _binding(raw, position) for position, raw in enumerate(raw_bindings)
    )
    seen: set[str] = set()
    for binding in bindings:
        if binding.id in seen:
            raise _fail(source, f"declares binding {binding.id!r} twice")
        seen.add(binding.id)
    return Bindings(consumer=consumer, bindings=bindings)


def load(workspace: pathlib.Path) -> Bindings | None:
    """Read ``.l9/bindings.yaml``; ``None`` when the consumer declares none."""
    path = workspace / PATH
    if not path.is_file():
        return None
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise BindingsInvalid(f"{path} is not readable YAML: {error}") from error
    return parse(document, str(path))
