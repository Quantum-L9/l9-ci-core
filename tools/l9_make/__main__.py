#!/usr/bin/env python3
"""Compile a closed capability plan into a deterministic ``Repo.mk`` adapter.

This is a Core-local renderer. It deliberately consumes a narrow, repository
approved plan and does not inspect the repository, infer capabilities, invoke
provider tooling, or implement publication. SDK-fed plan provenance is a later,
separately approved integration contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

PLAN_SCHEMA = "l9.make-capability-plan/v1"
TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
MAKE_TARGET_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")

STANDARD_BINDINGS: Mapping[str, str] = {
    "repo-setup": "setup",
    "repo-validate": "validate",
    "repo-check": "check",
    "repo-test": "test",
    "repo-clean": "clean",
    "repo-doctor": "doctor",
}
RESERVED_TARGETS = frozenset(
    {
        "help",
        "pr",
        "push",
        "start",
        "workspace-clean",
        "wiring-check",
    }
)


class CompilerError(ValueError):
    """Raised when a plan or local extension violates the compiler contract."""


def _require_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CompilerError(f"{path} must be an object")
    return value


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CompilerError(f"{path} must be a non-empty string without NUL")
    return value


def load_plan(path: pathlib.Path) -> dict[str, Any]:
    """Load and validate the closed V2 capability-plan contract."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CompilerError(f"capability plan is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise CompilerError(f"capability plan is invalid JSON: {error}") from error

    root = _require_mapping(raw, "plan")
    allowed = {"schema", "bindings"}
    if set(root) != allowed:
        raise CompilerError("plan must contain exactly schema and bindings")
    if root["schema"] != PLAN_SCHEMA:
        raise CompilerError(f"plan.schema must be {PLAN_SCHEMA}")
    bindings = root["bindings"]
    if not isinstance(bindings, list) or not bindings:
        raise CompilerError("plan.bindings must be a non-empty array")

    observed: dict[str, str] = {}
    for index, raw_binding in enumerate(bindings):
        binding = _require_mapping(raw_binding, f"plan.bindings[{index}]")
        if set(binding) != {"target", "command"}:
            raise CompilerError(
                f"plan.bindings[{index}] must contain exactly target and command"
            )
        target = _require_string(binding["target"], f"plan.bindings[{index}].target")
        command = _require_string(binding["command"], f"plan.bindings[{index}].command")
        if not TARGET_PATTERN.fullmatch(target):
            raise CompilerError(
                f"plan.bindings[{index}].target is not a safe Make target"
            )
        if target in RESERVED_TARGETS:
            raise CompilerError(f"plan.bindings[{index}].target is reserved: {target}")
        if target in observed:
            raise CompilerError(f"plan.bindings contains duplicate target: {target}")
        expected = STANDARD_BINDINGS.get(target)
        if expected is None:
            raise CompilerError(
                f"plan.bindings[{index}].target is not an approved Core binding"
            )
        if command != expected:
            raise CompilerError(
                f"plan.bindings[{index}].command must be {expected!r} for {target}"
            )
        observed[target] = command

    expected_targets = set(STANDARD_BINDINGS)
    if set(observed) != expected_targets:
        missing = sorted(expected_targets - set(observed))
        extra = sorted(set(observed) - expected_targets)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unapproved " + ", ".join(extra))
        raise CompilerError(
            "plan.bindings must define the exact Core binding set: "
            + "; ".join(details)
        )

    return {
        "schema": PLAN_SCHEMA,
        "bindings": [
            {"target": target, "command": observed[target]}
            for target in STANDARD_BINDINGS
        ],
    }


def plan_digest(plan: Mapping[str, object]) -> str:
    """Return a canonical digest independent of input JSON whitespace or ordering."""
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_local_extensions(
    path: pathlib.Path, generated_targets: Sequence[str]
) -> None:
    """Reject local target declarations that override generated or reserved names."""
    if not path.exists():
        return
    if not path.is_file():
        raise CompilerError(f"Repo.local.mk must be a regular file: {path}")
    forbidden = set(generated_targets) | set(RESERVED_TARGETS)
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = MAKE_TARGET_PATTERN.match(line)
        if match and match.group(1) in forbidden:
            raise CompilerError(
                f"Repo.local.mk:{number} overrides protected target {match.group(1)!r}"
            )


def render_repo_mk(plan: Mapping[str, object]) -> str:
    """Render the byte-stable generated adapter for an already validated plan."""
    bindings = plan["bindings"]
    assert isinstance(bindings, list)
    ordered = sorted(
        (binding for binding in bindings if isinstance(binding, dict)),
        key=lambda binding: str(binding["target"]),
    )
    target_lines = " \\\n\t".join(str(binding["target"]) for binding in ordered)
    lines = [
        "# GENERATED by Quantum-L9/l9-ci-core tools.l9_make. DO NOT EDIT.",
        f"# capability-plan-sha256: {plan_digest(plan)}",
        "# Repository-native targets belong in Repo.local.mk. Governance remains delegated by Makefile.",
        "PYTHON ?= python3",
        'L9_REPO := $(PYTHON) -m tools.l9_repo --workspace "$(CURDIR)"',
        "",
        ".PHONY: \\",
        f"\t{target_lines}",
        "",
    ]
    for binding in ordered:
        lines.append(f"{binding['target']}:")
        lines.append(f"\t@$(L9_REPO) {binding['command']}")
        lines.append("")
    return "\n".join(lines)


def _write_output(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render(
    plan_path: pathlib.Path, output: pathlib.Path, local: pathlib.Path | None
) -> None:
    plan = load_plan(plan_path)
    generated_targets = [str(binding["target"]) for binding in plan["bindings"]]
    if local is not None:
        validate_local_extensions(local, generated_targets)
    _write_output(output, render_repo_mk(plan))
    print(f"rendered {output}")


def check(
    plan_path: pathlib.Path, output: pathlib.Path, local: pathlib.Path | None
) -> None:
    plan = load_plan(plan_path)
    generated_targets = [str(binding["target"]) for binding in plan["bindings"]]
    if local is not None:
        validate_local_extensions(local, generated_targets)
    expected = render_repo_mk(plan)
    try:
        actual = output.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise CompilerError(f"generated output is missing: {output}") from error
    if actual != expected:
        raise CompilerError(f"generated output drifted: {output}; run l9_make render")
    print(f"verified {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("render", "check"):
        subparser = subcommands.add_parser(command)
        subparser.add_argument("--plan", type=pathlib.Path, required=True)
        subparser.add_argument("--output", type=pathlib.Path, required=True)
        subparser.add_argument("--local", type=pathlib.Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "render":
            render(args.plan, args.output, args.local)
        else:
            check(args.plan, args.output, args.local)
    except CompilerError as error:
        print(f"l9_make: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
