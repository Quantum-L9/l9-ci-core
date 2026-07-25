#!/usr/bin/env python3
"""Fail-closed Phase 4 release validation."""

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
FULL_ACTION_REF = re.compile(
    r"^\s*uses:\s*[^./\s][^@\s]*@[0-9a-fA-F]{40}\s*$",
    re.MULTILINE,
)


class ReleaseError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ReleaseError(f"{name} is required")
    return value


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


def emit(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def main() -> int:
    try:
        root = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
        tag = required("L9_RELEASE_TAG")
        expected = required("L9_EXPECTED_VERSION")
        if not SEMVER.fullmatch(tag):
            raise ReleaseError("release tag is not a valid semantic version")
        if not SEMVER.fullmatch(expected):
            raise ReleaseError("expected version is not a valid semantic version")
        normalized_tag = tag.removeprefix("v")
        normalized_expected = expected.removeprefix("v")
        if normalized_tag != normalized_expected:
            raise ReleaseError(
                f"release tag {tag!r} does not match expected version {expected!r}"
            )
        repo_spec = (root / ".l9/repo-spec.yaml").read_text(encoding="utf-8")
        architecture = (root / ".l9/architecture.yaml").read_text(encoding="utf-8")
        publication = (root / ".l9/publication-contract.yaml").read_text(
            encoding="utf-8"
        )
        required_fragments = {
            ".l9/repo-spec.yaml": (
                "version: 2.0.0",
                "phase_4:",
                "status: implemented",
            ),
            ".l9/architecture.yaml": (
                "phase: 4",
                "phase_4:",
                "status: implemented",
            ),
            ".l9/publication-contract.yaml": (
                "schema: l9.core-publication-contract/v1",
                "status: authoritative",
            ),
        }
        documents = {
            ".l9/repo-spec.yaml": repo_spec,
            ".l9/architecture.yaml": architecture,
            ".l9/publication-contract.yaml": publication,
        }
        for filename, fragments in required_fragments.items():
            for fragment in fragments:
                if fragment not in documents[filename]:
                    raise ReleaseError(f"{filename} is missing {fragment!r}")
        validate_external_action_pins(root)
        run_tests(root)
        emit("release-version", normalized_expected)
        print(f"Phase 4 release v{normalized_expected} is valid")
        return 0
    except ReleaseError as error:
        print(f"validate-release: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
