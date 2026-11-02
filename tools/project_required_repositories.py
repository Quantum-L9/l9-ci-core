#!/usr/bin/env python3
"""Project the repositories Core requires a V2 contract from into the verifier policy.

Which repositories are L9 members is not Core's to decide. The canonical
answer is ``semantics/repository_registry.yaml`` in Quantum-L9/.github
(``l9.repository-registry/global@1``): "a repository is a member because it is
listed here with a class_ref", and an unlisted repository has no implied
membership. This tool makes ``required_repositories`` in the
run-repository-verification policy a projection of that registry instead of a
hand-maintained list:

* every entry whose ``lifecycle`` is ``current`` is required; ``superseded``
  and ``retired`` entries are historical coordinates, not current operational
  sources, and are not required;
* the policy keeps its own ``schema`` and ``default_mode``; only
  ``required_repositories`` is projected;
* a receipt beside the policy records the exact source commit, path and
  sha256, the rule applied and the projected members, so the list is
  attributable and its drift is checkable.

The projection is non-canonical: the registry stays the authority. The policy
ships inside the pinned action (AGENTS.md §8), so a regenerated list reaches
the fleet only when org-ci.yml's Core action pins advance to a revision that
carries it (tools/advance_core_action_pins.py).

Usage:

    python3 tools/project_required_repositories.py --ref main           # fetch, write
    python3 tools/project_required_repositories.py --ref <sha> --check  # fetch, compare
    python3 tools/project_required_repositories.py --source FILE --commit <sha> [--check]
    python3 tools/project_required_repositories.py --verify             # offline consistency

Exit codes: ``0`` written or consistent, ``1`` drift or inconsistency, ``2``
invalid invocation or an unreadable/invalid source (fail closed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "run-repository-verification"
POLICY = ACTION / "contract-policy.json"
RECEIPT = ACTION / "required-repositories.receipt.json"

SOURCE_REPOSITORY = "Quantum-L9/.github"
SOURCE_URL = "https://github.com/Quantum-L9/.github.git"
SOURCE_PATH = "semantics/repository_registry.yaml"
REGISTRY_SCHEMA = "l9.repository-registry/v1"
REGISTRY_ARTIFACT = "l9.repository-registry/global@1"
RECEIPT_SCHEMA = "l9.required-repositories-projection/v1"
LIFECYCLES = frozenset({"current", "superseded", "retired"})
REQUIRED_LIFECYCLE = "current"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class ProjectionError(RuntimeError):
    """The source cannot be projected; the message names why (fail closed)."""


def project(registry: Any) -> list[str]:
    """Return the sorted ``owner/name`` coordinates the registry requires."""

    if not isinstance(registry, dict):
        raise ProjectionError("registry must be a mapping")
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise ProjectionError(f"registry schema must be {REGISTRY_SCHEMA!r}")
    if (
        registry.get("artifact_id") != REGISTRY_ARTIFACT
        or registry.get("canonical") is not True
    ):
        raise ProjectionError(f"registry must be the canonical {REGISTRY_ARTIFACT}")
    entries = registry.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise ProjectionError("registry lists no repositories")
    ids: set[str] = set()
    coordinates: set[str] = set()
    required: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ProjectionError("every registry entry must be a mapping")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id or entry_id in ids:
            raise ProjectionError(
                f"registry entry id {entry_id!r} is missing or duplicated"
            )
        ids.add(entry_id)
        coordinate = entry.get("coordinate")
        if not isinstance(coordinate, dict) or coordinate.get("provider") != "github":
            raise ProjectionError(f"{entry_id}: coordinate must name provider github")
        owner, name = coordinate.get("organization"), coordinate.get("repository")
        if not (isinstance(owner, str) and NAME.fullmatch(owner)):
            raise ProjectionError(f"{entry_id}: invalid organization {owner!r}")
        if not (isinstance(name, str) and NAME.fullmatch(name)):
            raise ProjectionError(f"{entry_id}: invalid repository {name!r}")
        full_name = f"{owner}/{name}"
        # The verifier matches case-insensitively, so two coordinates that
        # differ only in case would be one repository there: refuse them.
        if full_name.casefold() in coordinates:
            raise ProjectionError(f"{entry_id}: coordinate {full_name} is duplicated")
        coordinates.add(full_name.casefold())
        class_ref = entry.get("class_ref")
        if not isinstance(class_ref, str) or not class_ref:
            raise ProjectionError(f"{entry_id}: every member needs a class_ref")
        lifecycle = entry.get("lifecycle")
        if lifecycle not in LIFECYCLES:
            raise ProjectionError(f"{entry_id}: unknown lifecycle {lifecycle!r}")
        if lifecycle == REQUIRED_LIFECYCLE:
            required.append(full_name)
    if not required:
        raise ProjectionError("registry has no current repositories to require")
    return sorted(required, key=str.casefold)


def members_digest(members: list[str]) -> str:
    return hashlib.sha256(("\n".join(members) + "\n").encode()).hexdigest()


def render(
    members: list[str], policy: dict[str, Any], source: bytes, commit: str
) -> tuple[str, str]:
    """Return the projected policy text and its receipt text."""

    if not HEX40.fullmatch(commit):
        raise ProjectionError(
            f"source commit must be 40 lowercase hex characters, got {commit!r}"
        )
    projected = {**policy, "required_repositories": members}
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "non_canonical": True,
        "source": {
            "repository": SOURCE_REPOSITORY,
            "path": SOURCE_PATH,
            "commit": commit,
            "sha256": hashlib.sha256(source).hexdigest(),
            "artifact_id": REGISTRY_ARTIFACT,
        },
        "rule": f"lifecycle == {REQUIRED_LIFECYCLE}",
        "generated_by": "tools/project_required_repositories.py",
        "members": members,
        "members_sha256": members_digest(members),
    }
    return (
        json.dumps(projected, indent=2) + "\n",
        json.dumps(receipt, indent=2) + "\n",
    )


def fetch(ref: str) -> tuple[bytes, str]:
    """Read the registry at ``ref`` over git; return its bytes and exact commit."""

    with tempfile.TemporaryDirectory(prefix="l9-registry-") as tmp:
        store = Path(tmp) / "store.git"
        steps = (
            ["git", "init", "--quiet", "--bare", str(store)],
            [
                "git",
                "--git-dir",
                str(store),
                "fetch",
                "--quiet",
                "--depth",
                "1",
                SOURCE_URL,
                ref,
            ],
        )
        for argv in steps:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            if proc.returncode:
                raise ProjectionError(
                    f"cannot fetch {SOURCE_REPOSITORY}@{ref}: {proc.stderr.strip()[-300:]}"
                )
        commit = subprocess.run(
            ["git", "--git-dir", str(store), "rev-parse", "FETCH_HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
        blob = subprocess.run(
            ["git", "--git-dir", str(store), "show", f"{commit}:{SOURCE_PATH}"],
            capture_output=True,
            timeout=30,
        )
        if blob.returncode:
            raise ProjectionError(
                f"{SOURCE_REPOSITORY}@{commit[:12]} has no {SOURCE_PATH}"
            )
        return blob.stdout, commit


def verify() -> list[str]:
    """Offline consistency: the policy list is exactly the receipt's members."""

    problems = []
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    members = receipt.get("members")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        problems.append(f"receipt schema must be {RECEIPT_SCHEMA!r}")
    if not isinstance(members, list) or not members:
        problems.append("receipt lists no members")
        return problems
    if receipt.get("members_sha256") != members_digest(members):
        problems.append("receipt members_sha256 does not match its members")
    if members != sorted(members, key=str.casefold):
        problems.append("receipt members are not in canonical order")
    source = receipt.get("source", {})
    if (
        source.get("repository") != SOURCE_REPOSITORY
        or source.get("path") != SOURCE_PATH
    ):
        problems.append("receipt does not name the canonical registry")
    if not HEX40.fullmatch(str(source.get("commit", ""))):
        problems.append("receipt source commit is not a full SHA")
    if policy.get("required_repositories") != members:
        problems.append(
            "policy required_repositories differs from the receipt's projected members"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project required repositories from the L9 registry."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ref", help="Quantum-L9/.github ref to read the registry at")
    source.add_argument(
        "--source", type=Path, help="a local copy of repository_registry.yaml"
    )
    source.add_argument(
        "--verify", action="store_true", help="offline policy/receipt consistency"
    )
    parser.add_argument(
        "--commit",
        help="the .github commit --source was read at (required with --source)",
    )
    parser.add_argument(
        "--check", action="store_true", help="compare instead of writing"
    )
    args = parser.parse_args(argv)

    if args.verify:
        problems = verify()
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        if not problems:
            print("PASS: required_repositories matches its registry projection receipt")
        return 1 if problems else 0
    try:
        if args.source is not None:
            if not args.commit:
                parser.error("--source needs --commit")
            raw, commit = args.source.read_bytes(), args.commit
        else:
            raw, commit = fetch(args.ref)
        members = project(yaml.safe_load(raw))
        policy_text, receipt_text = render(
            members, json.loads(POLICY.read_text(encoding="utf-8")), raw, commit
        )
    except (ProjectionError, OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if args.check:
        drift = [
            path.relative_to(ROOT).as_posix()
            for path, text in ((POLICY, policy_text), (RECEIPT, receipt_text))
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
        for path in drift:
            print(
                f"FAIL: {path} drifts from {SOURCE_REPOSITORY}@{commit[:12]}",
                file=sys.stderr,
            )
        if not drift:
            print(
                f"PASS: {len(members)} required repositories match {SOURCE_REPOSITORY}@{commit[:12]}"
            )
        return 1 if drift else 0
    POLICY.write_text(policy_text, encoding="utf-8")
    RECEIPT.write_text(receipt_text, encoding="utf-8")
    print(
        f"WROTE: {len(members)} required repositories from {SOURCE_REPOSITORY}@{commit[:12]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
