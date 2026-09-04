#!/usr/bin/env python3
"""Fail-closed release validation for immutable Core releases.

A Core release is an immutable audit anchor (``.l9/release-plane.yaml``), not
the organization CI runtime channel: the GitHub organization ruleset binds
governed repositories to Core ``main`` directly, so nothing here moves a
major alias or publishes a consumer-facing ref.

The expected version is read from ``.l9/repo-spec.yaml`` unless the caller
overrides it, so the workflow never hard-codes a release number.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SEMVER = re.compile(
    r"^v?(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
REPO_SPEC_VERSION = re.compile(r"(?m)^\s+version:\s*['\"]?([^'\"\s]+)['\"]?\s*$")
CONTRACT_FRAGMENTS: dict[str, tuple[str, ...]] = {
    ".l9/repo-spec.yaml": (
        "schema: l9.repo-spec/v1",
        "phase_4:",
        "status: implemented",
    ),
    ".l9/architecture.yaml": (
        "schema: l9.architecture-spec/v1",
        "status: authoritative",
        "role: central-ci-orchestrator",
        "production_channel:",
    ),
    ".l9/publication-contract.yaml": (
        "schema: l9.core-publication-contract/v1",
        "status: authoritative",
    ),
    ".l9/release-plane.yaml": (
        "schema: l9.release-plane/v1",
        "status: authoritative",
        "runtime_authority: false",
        "moving_major_alias:",
        "enabled: false",
    ),
}


class ReleaseError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ReleaseError(f"{name} is required")
    return value


def optional(name: str) -> str:
    return os.environ.get(name, "").strip()


def declared_version(root: Path) -> str:
    """Return ``metadata.version`` from ``.l9/repo-spec.yaml``.

    The release checkout is bare (no PyYAML), so the value is read with a
    line pattern. The first ``version:`` key in the document is the metadata
    version; ``repo-spec`` declares no other ``version`` key.
    """
    text = (root / ".l9/repo-spec.yaml").read_text(encoding="utf-8")
    match = REPO_SPEC_VERSION.search(text)
    if match is None:
        raise ReleaseError(".l9/repo-spec.yaml declares no metadata version")
    return match.group(1)


def run_tests(root: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "--start-directory",
            "tests",
            "--pattern",
            "test_*.py",
            "--verbose",
        ],
        cwd=root,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError("repository validation suite failed")


def validate_external_action_pins(root: Path) -> None:
    invalid: list[str] = []
    for workflow in (root / ".github").rglob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("uses:"):
                continue
            reference = stripped.removeprefix("uses:").strip()
            if reference.startswith("./"):
                continue
            if not re.fullmatch(r"[^@\s]+@[0-9a-fA-F]{40}", reference):
                invalid.append(
                    f"{workflow.relative_to(root)}:{line_number}:{reference}"
                )
    if invalid:
        raise ReleaseError(
            "mutable external action references found:\n" + "\n".join(invalid)
        )


def validate_contracts(root: Path, version: str) -> None:
    for filename, fragments in CONTRACT_FRAGMENTS.items():
        path = root / filename
        if not path.is_file():
            raise ReleaseError(f"{filename} is missing")
        text = path.read_text(encoding="utf-8")
        for fragment in fragments:
            if fragment not in text:
                raise ReleaseError(f"{filename} is missing {fragment!r}")
    if declared_version(root) != version:
        raise ReleaseError(
            f".l9/repo-spec.yaml declares version {declared_version(root)!r}, "
            f"not the release version {version!r}"
        )


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def main() -> int:
    try:
        root = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
        tag = required("L9_RELEASE_TAG")
        if not SEMVER.fullmatch(tag):
            raise ReleaseError(
                "release tag is not a valid exact semantic version "
                "(moving major aliases are not releases)"
            )
        expected = optional("L9_EXPECTED_VERSION") or declared_version(root)
        if not SEMVER.fullmatch(expected):
            raise ReleaseError("expected version is not a valid semantic version")
        normalized_tag = tag.removeprefix("v")
        normalized_expected = expected.removeprefix("v")
        if normalized_tag != normalized_expected:
            raise ReleaseError(
                f"release tag {tag!r} does not match expected version {expected!r}"
            )
        validate_contracts(root, normalized_expected)
        validate_external_action_pins(root)
        run_tests(root)
        emit("release-version", normalized_expected)
        print(f"Core release v{normalized_expected} is valid")
        return 0
    except ReleaseError as error:
        print(f"validate-release: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
