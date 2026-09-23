#!/usr/bin/env python3
"""Fail-closed release validation for immutable Core releases.

A Core release is an immutable audit anchor (``.l9/release-plane.yaml``), not
the organization CI runtime channel: the GitHub organization ruleset binds
governed repositories to Core ``main`` directly, so nothing here moves a
major alias or publishes a consumer-facing ref.

The expected version is read from ``.l9/repo-spec.yaml`` unless the caller
overrides it, so the workflow never hard-codes a release number. Post-tag
validation also receives the annotated tag-object SHA and its peeled commit.
Only the sole-writer release script may omit both, and it must explicitly mark
that direct validator invocation as the pre-tag preflight where no tag exists.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# GitHub honours both spellings of a YAML executable surface: a workflow or
# composite action is loaded from `*.yml` and `*.yaml` identically. A scan that
# reads only one extension therefore leaves the other free to carry an unpinned
# external action into a release.
GITHUB_YAML_SUFFIXES = ("*.yml", "*.yaml")
SEMVER = re.compile(
    r"^v?(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
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


def git_output(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def annotated_tag_headers(root: Path, tag_object: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in git_output(root, "cat-file", "-p", tag_object).splitlines():
        if not line:
            break
        key, separator, value = line.partition(" ")
        if separator:
            headers[key] = value
    return headers


def validate_release_identity(root: Path, tag: str) -> None:
    """Bind post-tag validation to one annotated tag object and peeled commit.

    The workflow resolves the exact remote tag before checking out released
    code. This second boundary check ensures the composite action cannot be
    invoked without that identity, with a partial identity, or against a
    different checkout. The sole-writer release preflight explicitly opts into
    the only exception: neither identity exists before the tag is created.
    """
    tag_object = optional("L9_RELEASE_TAG_OBJECT").lower()
    release_commit = optional("L9_RELEASE_COMMIT").lower()
    preflight = optional("L9_RELEASE_PREFLIGHT").lower()
    if preflight not in {"true", "false"}:
        raise ReleaseError("L9_RELEASE_PREFLIGHT must be exactly 'true' or 'false'")
    if preflight == "true":
        if tag_object or release_commit:
            raise ReleaseError("release preflight must not supply post-tag identity")
        return
    if not FULL_SHA.fullmatch(tag_object):
        raise ReleaseError("L9_RELEASE_TAG_OBJECT must be a full 40-character SHA")
    if not FULL_SHA.fullmatch(release_commit):
        raise ReleaseError("L9_RELEASE_COMMIT must be a full 40-character SHA")
    if git_output(root, "cat-file", "-t", tag_object) != "tag":
        raise ReleaseError("L9_RELEASE_TAG_OBJECT is not an annotated tag object")
    headers = annotated_tag_headers(root, tag_object)
    if headers.get("tag") != tag:
        raise ReleaseError(
            "annotated tag object's embedded name does not match release tag"
        )
    if headers.get("type") != "commit":
        raise ReleaseError("release tag must point directly to a commit")
    direct_target = headers.get("object", "")
    if not FULL_SHA.fullmatch(direct_target):
        raise ReleaseError("annotated tag object has no full direct target SHA")
    if git_output(root, "cat-file", "-t", direct_target) != "commit":
        raise ReleaseError("annotated tag target is not a commit")
    if direct_target != release_commit:
        raise ReleaseError("annotated tag object does not peel to L9_RELEASE_COMMIT")
    checkout = git_output(root, "rev-parse", "HEAD^{commit}")
    if checkout != release_commit:
        raise ReleaseError("checked-out HEAD is not L9_RELEASE_COMMIT")


def github_yaml_surfaces(root: Path) -> list[Path]:
    """Return every GitHub YAML surface beneath ``.github``.

    One canonical discovery mechanism covers reusable workflows
    (``.github/workflows/**``), composite actions
    (``.github/actions/**/action.yml`` and ``action.yaml``), and any other
    executable YAML GitHub may load from the directory. Separate
    workflow/action scanners are how one of the two extensions ends up
    unenforced, so there is deliberately only one.
    """
    base = root / ".github"
    if not base.is_dir():
        return []
    return sorted(
        path
        for pattern in GITHUB_YAML_SUFFIXES
        for path in base.rglob(pattern)
        if path.is_file()
    )


def validate_external_action_pins(root: Path) -> None:
    invalid: list[str] = []
    for workflow in github_yaml_surfaces(root):
        text = workflow.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            # A step may open with `- uses:` or carry `uses:` after `- name:`;
            # both forms are references and both must be validated.
            stripped = line.strip().removeprefix("- ").strip()
            if not stripped.startswith("uses:"):
                continue
            # `uses: owner/action@<sha> # vX.Y.Z` is the pinning convention;
            # the trailing version comment is not part of the reference.
            reference = stripped.removeprefix("uses:").split("#", 1)[0].strip()
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
        validate_release_identity(root, tag)
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
