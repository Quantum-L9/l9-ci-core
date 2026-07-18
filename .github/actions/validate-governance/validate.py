#!/usr/bin/env python3
"""Validate all Phase 3 governance documents and transitions."""

from __future__ import annotations
import importlib.util
import json
import os
import sys
from pathlib import Path

RESOLVER = Path(__file__).resolve().parents[1] / "resolve-governance" / "resolve.py"
spec = importlib.util.spec_from_file_location("l9_resolve_governance", RESOLVER)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load governance resolver")
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


def validate_promotions(documents: dict[str, object]) -> None:
    promotion = documents["promotion-policy.yaml"]
    transitions = promotion.get("transitions")
    if not isinstance(transitions, dict):
        raise resolver.GovernanceError("promotion transitions must be an object")
    for source, targets in transitions.items():
        if source not in resolver.ALLOWED_MODES:
            raise resolver.GovernanceError(f"invalid promotion source: {source}")
        if not isinstance(targets, list):
            raise resolver.GovernanceError(
                f"promotion targets for {source} must be a list"
            )
        for target in targets:
            if target not in resolver.ALLOWED_MODES:
                raise resolver.GovernanceError(f"invalid promotion target: {target}")
            if target == source:
                raise resolver.GovernanceError(
                    f"self-transition is prohibited: {source}"
                )


def main() -> int:
    try:
        root = resolver.workspace_path(
            os.environ.get(
                "L9_GOVERNANCE_ROOT",
                ".github/governance",
            )
        )
        documents = resolver.load_documents(root)
        profiles = documents["execution-profiles.yaml"]["profiles"]
        if set(profiles) != {
            "pr_fast",
            "merge",
            "nightly",
            "release",
            "supply_chain",
        }:
            raise resolver.GovernanceError("execution profile set is incomplete")
        for profile_name, profile in profiles.items():
            for provider in profile["providers"]:
                allowed_events = profile["allowed_events"]
                resolver.validate_profile(
                    documents,
                    profile_name,
                    provider,
                    allowed_events[0],
                )
                resolver.resolve_mode(
                    documents,
                    profile_name,
                    provider,
                    profile["default_mode"],
                )
                resolver.resolve_requiredness(
                    documents,
                    profile_name,
                    provider,
                )
                resolver.resolve_policy(
                    documents,
                    profile_name,
                    root,
                )
        resolver.applicable_waivers(
            documents,
            profile="pr_fast",
            provider="semgrep",
            repository="Quantum-L9/l9-ci-core",
            ref="refs/heads/main",
            today=resolver.evaluation_date(),
        )
        validate_promotions(documents)
        print(
            json.dumps(
                {
                    "schema": "l9.governance-validation-result/v1",
                    "status": "valid",
                    "digest": resolver.canonical_digest(root),
                },
                sort_keys=True,
            )
        )
        return 0
    except resolver.GovernanceError as error:
        print(f"validate-governance: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
