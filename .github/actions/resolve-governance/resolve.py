#!/usr/bin/env python3
"""Resolve Core-owned governance without interpreting SDK artifacts."""

from __future__ import annotations
import datetime as dt
import fnmatch
import hashlib
import json
import os
import sys
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


class GovernanceError(RuntimeError):
    pass


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise GovernanceError(f"{name} is required")
    return value


def workspace_path(value: str, *, must_exist: bool = True) -> Path:
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
        raise GovernanceError(
            "governance path must remain inside GITHUB_WORKSPACE"
        ) from error
    if must_exist and not path.exists():
        raise GovernanceError(f"path does not exist: {path}")
    return path


def load_documents(root: Path) -> dict[str, Any]:
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
    documents: dict[str, Any],
    profile_name: str,
    provider: str,
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


def resolve_policy(
    documents: dict[str, Any],
    profile_name: str,
    governance_root: Path,
) -> str:
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
        return ""
    policy_path = workspace_path(policy)
    if not policy_path.is_file():
        raise GovernanceError(f"SDK policy does not exist: {policy_path}")
    # Core validates only path existence. The SDK owns policy semantics.
    return policy_path.relative_to(
        Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    ).as_posix()


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


def canonical_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for filename in sorted(EXPECTED_SCHEMAS):
        path = root / filename
        digest.update(filename.encode("utf-8"))
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
        governance_root = workspace_path(required_environment("L9_GOVERNANCE_ROOT"))
        documents = load_documents(governance_root)
        profile = validate_profile(
            documents,
            profile_name,
            provider,
            event_name,
        )
        mode = resolve_mode(
            documents,
            profile_name,
            provider,
            profile["default_mode"],
        )
        required = resolve_requiredness(
            documents,
            profile_name,
            provider,
        )
        policy = resolve_policy(
            documents,
            profile_name,
            governance_root,
        )
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
        emit("waiver-ids", ",".join(waivers))
        emit("governance-digest", canonical_digest(governance_root))
        return 0
    except GovernanceError as error:
        print(f"resolve-governance: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
