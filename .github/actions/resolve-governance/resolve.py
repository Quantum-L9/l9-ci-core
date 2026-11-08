#!/usr/bin/env python3
"""Resolve centrally owned Core governance without interpreting SDK artifacts."""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

EXPECTED_SCHEMAS = {
    "execution-profiles.yaml": "l9.execution-profiles/v1",
    "rule-modes.yaml": "l9.rule-modes/v1",
    "provider-requiredness.yaml": "l9.provider-requiredness/v1",
    "quality-thresholds.yaml": "l9.quality-threshold-selection/v1",
    "waivers.yaml": "l9.waivers/v1",
    "promotion-policy.yaml": "l9.promotion-policy/v1",
}
ALLOWED_MODES = {"blocking", "advisory", "shadow", "disabled"}
ALLOWED_SDK_PROFILES = {"ci_fast", "ci_deep"}
IDENTITY_MAP_FILENAMES = ("python.yaml", "typescript.yaml")
SDK_POLICY_SCHEMA = "l9.finding-policy/v1"
CONSTELLATION_RULES_FILENAME = "constellation-node-rules.json"
CONSTELLATION_RULES_SCHEMA = "l9.constellation-node-rules/v1"
SCOPE_NODE = "node"
SCOPE_NON_NODE = "non-node"


class GovernanceError(RuntimeError):
    pass


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise GovernanceError(f"{name} is required")
    return value


def workspace_directory(workspace: Path, relative: Path) -> Path:
    """Create a workspace-contained directory without following symlinks."""
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise GovernanceError("staging directory must be a non-empty relative path")
    destination = workspace
    for component in relative.parts:
        destination = destination / component
        if destination.is_symlink():
            raise GovernanceError("staging directory must not contain a symlink")
        if destination.exists():
            if not destination.is_dir():
                raise GovernanceError("staging directory component is not a directory")
        else:
            destination.mkdir()
        try:
            destination.resolve().relative_to(workspace)
        except ValueError as error:
            raise GovernanceError(
                "staging directory must remain inside GITHUB_WORKSPACE"
            ) from error
    return destination


def core_defaults_path() -> Path:
    path = Path(__file__).resolve().parent / "defaults"
    if not path.is_dir():
        raise GovernanceError("Core bundled governance defaults missing")
    return path


def core_identity_maps_path() -> Path:
    path = Path(__file__).resolve().parent / "identity-maps"
    if not path.is_dir():
        raise GovernanceError("Core bundled Semgrep identity maps missing")
    return path


def core_constellation_rules_path() -> Path:
    return Path(__file__).resolve().parent / CONSTELLATION_RULES_FILENAME


def _string_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
    ):
        raise GovernanceError(f"{label} must be a non-empty list of unique strings")
    return list(value)


def load_constellation_rules(path: Path | None = None) -> dict[str, Any]:
    """Load the Core-owned scope of the Gate routing rules.

    Gate routing obligations belong to Constellation nodes (Quantum-L9/.github
    ProductKind ``node``). The document names the admitted ProductKinds, the
    kind that is bound to Gate, and the SDK rules that enforce that binding.
    """
    source = path or core_constellation_rules_path()
    if source.is_symlink() or not source.is_file():
        raise GovernanceError(f"Core constellation scope missing: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GovernanceError(
            f"invalid constellation scope {source}: {error}"
        ) from error
    if (
        not isinstance(document, dict)
        or document.get("schema") != CONSTELLATION_RULES_SCHEMA
    ):
        raise GovernanceError(
            f"constellation scope must use {CONSTELLATION_RULES_SCHEMA}"
        )
    kinds = document.get("product_kinds")
    if not isinstance(kinds, dict):
        raise GovernanceError("constellation scope product_kinds must be an object")
    admitted = _string_list(kinds.get("admitted"), "product_kinds.admitted")
    gate_bound = kinds.get("gate_bound")
    if gate_bound not in admitted:
        raise GovernanceError("product_kinds.gate_bound must be an admitted kind")
    rules = _string_list(document.get("gate_routing_rules"), "gate_routing_rules")
    policy_key = document.get("undeclared_policy_key")
    if not isinstance(policy_key, str) or not policy_key.strip():
        raise GovernanceError("undeclared_policy_key must be a non-empty string")
    mode = document.get("undeclared_mode")
    if mode not in ALLOWED_MODES or mode == "blocking":
        raise GovernanceError("undeclared_mode must be a non-blocking rule mode")
    return {
        "admitted": admitted,
        "gate_bound": gate_bound,
        "rules": rules,
        "policy_key": policy_key,
        "mode": mode,
    }


def constellation_scope(product_kind: str, scope_rules: dict[str, Any]) -> str:
    """Map a declared ProductKind onto the Gate routing scope.

    An empty declaration is valid and is not a node. A declared kind must be
    one Quantum-L9/.github admits; anything else fails closed.
    """
    if not product_kind:
        return SCOPE_NON_NODE
    if product_kind not in scope_rules["admitted"]:
        raise GovernanceError(
            f"product kind {product_kind!r} is not admitted: "
            f"{sorted(scope_rules['admitted'])}"
        )
    return SCOPE_NODE if product_kind == scope_rules["gate_bound"] else SCOPE_NON_NODE


def scope_policy(
    selected_policy: tuple[str, bytes] | None,
    scope: str,
    scope_rules: dict[str, Any],
) -> tuple[str, bytes] | None:
    """Return the SDK policy for this repository's Gate routing scope.

    A node receives the bundled policy byte-for-byte. Any other repository
    receives the same policy with only the Gate routing rules classified as
    the undeclared mode, so they run and are reported but cannot block.
    """
    if selected_policy is None or scope == SCOPE_NODE:
        return selected_policy
    filename, payload = selected_policy
    document = json.loads(payload)
    rules = document.get("rules", {})
    if not isinstance(rules, dict):
        raise GovernanceError("SDK policy rules must be an object")
    for rule_id in scope_rules["rules"]:
        rules[rule_id] = {
            "policy_key": scope_rules["policy_key"],
            "mode": scope_rules["mode"],
        }
    document["rules"] = rules
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise GovernanceError("SDK policy metadata must be an object")
    metadata["constellation_scope"] = SCOPE_NON_NODE
    return filename, (json.dumps(document, indent=2) + "\n").encode("utf-8")


def load_documents(root: Path) -> dict[str, Any]:
    """Load the six Core control documents from a governance directory.

    Supplemental files may coexist in repository-local dogfood directories
    (for example an SDK policy document). They are not part of the Core
    governance digest and cannot become control documents implicitly. The
    bundled @core-defaults directory is separately tested to contain exactly
    the six files declared by EXPECTED_SCHEMAS.
    """
    documents: dict[str, Any] = {}
    for filename, expected_schema in EXPECTED_SCHEMAS.items():
        path = root / filename
        if not path.is_file():
            raise GovernanceError(f"missing governance file: {path}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise GovernanceError(
                f"invalid governance JSON/YAML document {path}: {error}"
            ) from error
        if not isinstance(document, dict):
            raise GovernanceError(f"{path} must contain an object")
        if document.get("schema") != expected_schema:
            raise GovernanceError(
                f"{path} has unsupported schema {document.get('schema')!r}"
            )
        documents[filename] = document
    return documents


def parse_date(value: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise GovernanceError(f"{label} must be an ISO-8601 date") from error


def evaluation_date() -> dt.date:
    configured = os.environ.get("L9_EVALUATION_DATE", "").strip()
    if configured:
        return parse_date(configured, "evaluation-date")
    return dt.datetime.now(dt.timezone.utc).date()


def validate_profile(
    documents: dict[str, Any],
    profile_name: str,
    provider: str,
    event_name: str,
) -> dict[str, Any]:
    profiles = documents["execution-profiles.yaml"].get("profiles")
    if not isinstance(profiles, dict) or profile_name not in profiles:
        raise GovernanceError(f"unknown execution profile: {profile_name}")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise GovernanceError(f"profile {profile_name!r} must be an object")
    sdk_profile = profile.get("sdk_profile")
    if sdk_profile not in ALLOWED_SDK_PROFILES:
        raise GovernanceError(f"profile {profile_name!r} has unsupported SDK profile")
    strict = profile.get("strict")
    if not isinstance(strict, bool):
        raise GovernanceError(f"profile {profile_name!r} strict must be boolean")
    providers = profile.get("providers")
    if not isinstance(providers, list) or not all(
        isinstance(item, str) for item in providers
    ):
        raise GovernanceError(f"profile {profile_name!r} providers must be strings")
    if provider not in providers:
        raise GovernanceError(
            f"provider {provider!r} is not declared by profile {profile_name!r}"
        )
    allowed_events = profile.get("allowed_events")
    if not isinstance(allowed_events, list) or event_name not in allowed_events:
        raise GovernanceError(
            f"event {event_name!r} is not allowed for profile {profile_name!r}"
        )
    mode = profile.get("default_mode")
    if mode not in ALLOWED_MODES:
        raise GovernanceError(f"profile {profile_name!r} has invalid default mode")
    return profile


def resolve_mode(
    documents: dict[str, Any],
    profile_name: str,
    provider: str,
    default_mode: str,
) -> str:
    modes = documents["rule-modes.yaml"]
    allowed = modes.get("allowed_modes")
    if not isinstance(allowed, list) or set(allowed) != ALLOWED_MODES:
        raise GovernanceError("rule-modes allowed_modes is invalid")
    defaults = modes.get("defaults")
    if not isinstance(defaults, dict):
        raise GovernanceError("rule-modes defaults must be an object")
    mode = defaults.get(profile_name, default_mode)
    overrides = modes.get("provider_overrides")
    if not isinstance(overrides, dict):
        raise GovernanceError("rule-modes provider_overrides must be an object")
    provider_overrides = overrides.get(provider, {})
    if not isinstance(provider_overrides, dict):
        raise GovernanceError(f"provider override for {provider!r} must be an object")
    mode = provider_overrides.get(profile_name, mode)
    if mode not in ALLOWED_MODES:
        raise GovernanceError(f"resolved mode {mode!r} is unsupported")
    return mode


def resolve_requiredness(
    documents: dict[str, Any], profile_name: str, provider: str
) -> bool:
    profiles = documents["provider-requiredness.yaml"].get("profiles")
    if not isinstance(profiles, dict):
        raise GovernanceError("provider-requiredness profiles must be an object")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise GovernanceError(f"requiredness is missing profile {profile_name!r}")
    required = profile.get(provider)
    if not isinstance(required, bool):
        raise GovernanceError(f"requiredness is missing provider {provider!r}")
    return required


def _bundled_policy_path(governance_root: Path, policy: str) -> Path:
    """Resolve a Core-bundled policy name against the governance root.

    ``defaults/`` holds the six control documents only. The Semgrep policy
    lives next to ``defaults/`` so the exact-six test stays true.
    """
    if Path(policy).is_absolute():
        raise GovernanceError("sdk_policy must be a relative filename")
    name = Path(policy).name
    if name != policy or name in {".", ".."}:
        raise GovernanceError("sdk_policy must be a bare filename")
    for candidate in (governance_root / name, governance_root.parent / name):
        if candidate.is_symlink():
            raise GovernanceError("SDK policy source must not be a symlink")
        if candidate.is_file():
            return candidate
    raise GovernanceError(f"SDK policy does not exist: {name}")


def _validated_sdk_policy_bytes(path: Path) -> bytes:
    """Validate only the SDK-owned policy's transport envelope."""
    payload = path.read_bytes()
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GovernanceError(f"invalid SDK policy JSON {path}: {error}") from error
    if not isinstance(document, dict):
        raise GovernanceError(f"SDK policy {path} must contain an object")
    if document.get("schema") != SDK_POLICY_SCHEMA:
        raise GovernanceError(
            f"SDK policy {path} has unsupported schema {document.get('schema')!r}"
        )
    return payload


def _stage_sdk_policy(source: Path, payload: bytes, workspace: Path) -> str:
    destination = workspace_directory(
        workspace,
        Path(".l9/runtime/org-governance"),
    )
    target = destination / source.name
    if target.is_symlink():
        raise GovernanceError("SDK policy destination must not be a symlink")
    if target.exists() and not target.is_file():
        raise GovernanceError("SDK policy destination is not a file")

    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination,
            prefix=f".{source.name}.",
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    except OSError as error:
        raise GovernanceError(
            f"could not stage SDK policy {source}: {error}"
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target.relative_to(workspace).as_posix()


def select_policy(
    documents: dict[str, Any], profile_name: str, governance_root: Path
) -> tuple[str, bytes] | None:
    profiles = documents["quality-thresholds.yaml"].get("profiles")
    if not isinstance(profiles, dict):
        raise GovernanceError("quality-thresholds profiles must be an object")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise GovernanceError(
            f"quality threshold selection is missing {profile_name!r}"
        )
    policy = profile.get("sdk_policy")
    if not isinstance(policy, str):
        raise GovernanceError("sdk_policy must be a string")
    if not policy:
        return None
    bundled = _bundled_policy_path(governance_root, policy)
    return policy, _validated_sdk_policy_bytes(bundled)


def stage_selected_policy(
    selected_policy: tuple[str, bytes] | None,
    governance_root: Path,
) -> str:
    if selected_policy is None:
        return ""
    policy, payload = selected_policy
    workspace_raw = os.environ.get("GITHUB_WORKSPACE", "").strip()
    source = _bundled_policy_path(governance_root, policy)
    if not workspace_raw:
        if payload == source.read_bytes():
            return source.resolve().as_posix()
        # A scoped policy has no bundled file of its own; outside a workspace
        # it is written to a private temporary directory instead.
        target = Path(tempfile.mkdtemp(prefix="l9-org-governance-")) / source.name
        target.write_bytes(payload)
        return target.as_posix()
    workspace = Path(workspace_raw).resolve()
    return _stage_sdk_policy(source, payload, workspace)


def resolve_policy(
    documents: dict[str, Any], profile_name: str, governance_root: Path
) -> str:
    return stage_selected_policy(
        select_policy(documents, profile_name, governance_root),
        governance_root,
    )


def _validate_identity_map(path: Path) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GovernanceError(f"invalid Core identity map {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != "l9.identity-map/v1":
        raise GovernanceError(f"Core identity map has unsupported schema: {path}")
    metadata = document.get("metadata")
    rules = document.get("rules")
    if (
        not isinstance(metadata, dict)
        or metadata.get("provider_id") != "semgrep"
        or not isinstance(rules, dict)
        or not rules
    ):
        raise GovernanceError(f"Core identity map is malformed: {path}")
    for provider_rule_id, entry in rules.items():
        if (
            not isinstance(provider_rule_id, str)
            or not provider_rule_id
            or not isinstance(entry, dict)
            or not isinstance(entry.get("canonical_rule_id"), str)
            or not entry["canonical_rule_id"]
        ):
            raise GovernanceError(f"Core identity map has invalid rule entry: {path}")


def stage_identity_maps() -> str:
    """Stage Core-reviewed Semgrep identity maps inside the caller workspace.

    The SDK remains the identity-resolution authority. Core only transports the
    reviewed maps selected by the workflow's already bounded language input.
    Both maps are staged before language detection so a single governance pass
    can serve Python and TypeScript callers without reopening consumer policy.
    """
    source_root = core_identity_maps_path()
    source_paths = [source_root / filename for filename in IDENTITY_MAP_FILENAMES]
    for source in source_paths:
        if not source.is_file():
            raise GovernanceError(f"Core identity map missing: {source}")
        _validate_identity_map(source)

    workspace_raw = os.environ.get("GITHUB_WORKSPACE", "").strip()
    if not workspace_raw:
        return source_root.resolve().as_posix()
    workspace = Path(workspace_raw).resolve()
    destination = workspace_directory(
        workspace,
        Path(".l9/runtime/org-governance/semgrep-identity-maps"),
    )
    for source in source_paths:
        target = destination / source.name
        if target.is_symlink():
            raise GovernanceError("identity-map destination must not be a symlink")
        if target.exists():
            if not target.is_file():
                raise GovernanceError("identity-map destination is not a file")
            target.unlink()
        target.write_bytes(source.read_bytes())
    return destination.relative_to(workspace).as_posix()


def applicable_waivers(
    documents: dict[str, Any],
    *,
    profile: str,
    provider: str,
    repository: str,
    ref: str,
    today: dt.date,
) -> list[str]:
    entries = documents["waivers.yaml"].get("waivers")
    if not isinstance(entries, list):
        raise GovernanceError("waivers must be an array")
    active: list[str] = []
    seen: set[str] = set()
    for index, waiver in enumerate(entries):
        if not isinstance(waiver, dict):
            raise GovernanceError(f"waiver {index} must be an object")
        waiver_id = waiver.get("id")
        if not isinstance(waiver_id, str) or not waiver_id:
            raise GovernanceError(f"waiver {index} has invalid id")
        if waiver_id in seen:
            raise GovernanceError(f"duplicate waiver id: {waiver_id}")
        seen.add(waiver_id)
        owner = waiver.get("owner")
        reason = waiver.get("reason")
        created = waiver.get("created")
        expires = waiver.get("expires")
        if not isinstance(owner, str) or not owner:
            raise GovernanceError(f"waiver {waiver_id} has no owner")
        if not isinstance(reason, str) or not reason:
            raise GovernanceError(f"waiver {waiver_id} has no reason")
        if not isinstance(created, str) or not isinstance(expires, str):
            raise GovernanceError(
                f"waiver {waiver_id} requires created and expires dates"
            )
        created_date = parse_date(created, f"waiver {waiver_id} created")
        expiry_date = parse_date(expires, f"waiver {waiver_id} expires")
        if expiry_date < created_date:
            raise GovernanceError(f"waiver {waiver_id} expires before it was created")
        if expiry_date < today:
            raise GovernanceError(f"waiver {waiver_id} is expired")
        scope = waiver.get("scope")
        if not isinstance(scope, dict):
            raise GovernanceError(f"waiver {waiver_id} scope must be an object")
        repositories = scope.get("repositories", [])
        refs = scope.get("refs", [])
        profiles = scope.get("profiles", [])
        providers = scope.get("providers", [])
        fields = {
            "repositories": repositories,
            "refs": refs,
            "profiles": profiles,
            "providers": providers,
        }
        for field_name, values in fields.items():
            if not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                raise GovernanceError(
                    f"waiver {waiver_id} {field_name} must be strings"
                )
        matches_repository = not repositories or any(
            fnmatch.fnmatch(repository, value) for value in repositories
        )
        matches_ref = not refs or any(fnmatch.fnmatch(ref, value) for value in refs)
        matches_profile = not profiles or profile in profiles
        matches_provider = not providers or provider in providers
        if matches_repository and matches_ref and matches_profile and matches_provider:
            active.append(waiver_id)
    return sorted(active)


def canonical_digest(
    root: Path,
    selected_policy: tuple[str, bytes] | None,
    identity_maps_root: Path | None = None,
) -> str:
    digest = hashlib.sha256()
    for filename in sorted(EXPECTED_SCHEMAS):
        path = root / filename
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    digest.update(b"selected-sdk-policy/filename\0")
    if selected_policy is not None:
        filename, payload = selected_policy
        digest.update(filename.encode("utf-8"))
    digest.update(b"\0selected-sdk-policy/bytes\0")
    if selected_policy is not None:
        digest.update(payload)
    digest.update(b"\0")
    maps_root = identity_maps_root or core_identity_maps_path()
    for filename in IDENTITY_MAP_FILENAMES:
        path = maps_root / filename
        if not path.is_file():
            raise GovernanceError(f"Core identity map missing: {path}")
        _validate_identity_map(path)
        digest.update(f"identity-maps/{filename}".encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def emit(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    try:
        profile_name = required_environment("L9_PROFILE")
        provider = required_environment("L9_PROVIDER")
        event_name = required_environment("L9_EVENT_NAME")
        repository = required_environment("L9_REPOSITORY")
        ref = required_environment("L9_REF")
        product_kind = os.environ.get("L9_PRODUCT_KIND", "").strip()
        governance_root = core_defaults_path()
        documents = load_documents(governance_root)
        profile = validate_profile(documents, profile_name, provider, event_name)
        mode = resolve_mode(documents, profile_name, provider, profile["default_mode"])
        required = resolve_requiredness(documents, profile_name, provider)
        scope_rules = load_constellation_rules()
        scope = constellation_scope(product_kind, scope_rules)
        selected_policy = scope_policy(
            select_policy(documents, profile_name, governance_root),
            scope,
            scope_rules,
        )
        policy = stage_selected_policy(selected_policy, governance_root)
        identity_map_directory = stage_identity_maps()
        waivers = applicable_waivers(
            documents,
            profile=profile_name,
            provider=provider,
            repository=repository,
            ref=ref,
            today=evaluation_date(),
        )
        enabled = mode != "disabled"
        if not enabled and required:
            raise GovernanceError("a required provider cannot resolve to disabled")
        emit("sdk-profile", profile["sdk_profile"])
        emit("mode", mode)
        emit("enabled", str(enabled).lower())
        emit("strict", str(profile["strict"]).lower())
        emit("required-provider", str(required).lower())
        emit("sdk-policy", policy)
        emit("constellation-scope", scope)
        emit("identity-map-directory", identity_map_directory)
        emit("waiver-ids", ",".join(waivers))
        emit(
            "governance-digest",
            canonical_digest(governance_root, selected_policy),
        )
        return 0
    except GovernanceError as error:
        print(f"resolve-governance: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
