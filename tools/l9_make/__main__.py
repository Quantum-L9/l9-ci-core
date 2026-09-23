#!/usr/bin/env python3
"""Compile an authoritative capability plan into deterministic ``Repo.mk``.

The compiler is deliberately narrow. It validates an already-resolved plan,
renders a repository-local Make adapter, and proves the generated adapter has
not drifted. It neither detects repository capabilities nor implements
publication: `make pr` remains a one-line delegation to the Governance SSOT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from typing import Any, cast

PLAN_SCHEMA = "l9.make-plan/v1"
PLAN_SCHEMA_PATH = pathlib.Path(__file__).with_name("make-plan.schema.json")
REPO_MK_TEMPLATE_PATH = pathlib.Path(__file__).with_name("Repo.mk.template")
MAKEFILE_TEMPLATE_PATH = pathlib.Path(__file__).with_name("Makefile.template")
TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
MAKE_TARGET_PATTERN = re.compile(
    r"^\s*([A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+)*)\s*::?\s*(?:#.*)?$"
)

# The standard local vocabulary is universal. Individual capabilities remain
# explicit about whether a repository supports, does not require, or does not
# provide the operation.
STANDARD_CAPABILITIES = (
    "doctor",
    "setup",
    "build",
    "lint",
    "test",
    "validate",
    "check",
    "package",
    "generate",
    "benchmark",
    "status",
    "clean",
)
CAPABILITY_STATES = frozenset({"supported", "not_required", "unsupported"})
COMPATIBILITY_TARGETS = frozenset({"check"})
ROOT_TARGETS = frozenset({"help", "capabilities", *STANDARD_CAPABILITIES, "pr"})
RESERVED_GOVERNANCE_TARGETS = frozenset(
    {"pr", "push", "release", "deploy", "start", "workspace-clean", "wiring-check"}
)


class CompilerError(ValueError):
    """Raised when a capability plan or extension violates the contract."""


def _require_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CompilerError(f"{path} must be an object")
    return value


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CompilerError(f"{path} must be a non-empty string without NUL")
    if "\r" in value or "\n" in value:
        raise CompilerError(f"{path} must not contain a line break")
    return value


def _load_json(path: pathlib.Path, description: str) -> Mapping[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CompilerError(f"{description} is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise CompilerError(f"{description} is invalid JSON: {error}") from error
    return _require_mapping(raw, description)


def _validate_against_schema(plan: Mapping[str, object]) -> None:
    """Apply the shipped Draft 2020-12 schema before semantic normalization."""
    try:
        import jsonschema
    except ModuleNotFoundError as error:
        raise CompilerError("jsonschema is required to validate make plans") from error

    schema = _load_json(PLAN_SCHEMA_PATH, "make-plan schema")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(plan),
            key=lambda error: list(error.path),
        )
    except (
        Exception
    ) as error:  # pragma: no cover - schema defects are infrastructure failures
        raise CompilerError(f"make-plan schema validation failed: {error}") from error
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or 'plan'}: {error.message}"
            for error in errors
        )
        raise CompilerError(f"make-plan contract violation: {detail}")


def _normalized_capability(raw: object, index: int) -> dict[str, Any]:
    capability = _require_mapping(raw, f"plan.capabilities[{index}]")
    name = _require_string(capability.get("name"), f"plan.capabilities[{index}].name")
    if not TARGET_PATTERN.fullmatch(name) or name not in STANDARD_CAPABILITIES:
        raise CompilerError(
            f"plan.capabilities[{index}].name is not a standard capability: {name!r}"
        )
    state = _require_string(
        capability.get("state"), f"plan.capabilities[{index}].state"
    )
    if state not in CAPABILITY_STATES:
        raise CompilerError(f"plan.capabilities[{index}].state is invalid: {state!r}")
    kind = _require_string(capability.get("kind"), f"plan.capabilities[{index}].kind")
    if name in COMPATIBILITY_TARGETS:
        if kind != "compatibility_alias":
            raise CompilerError(
                f"plan.capabilities[{index}].kind must be compatibility_alias"
            )
    elif kind != "native_binding":
        raise CompilerError(f"plan.capabilities[{index}].kind must be native_binding")

    provenance = _require_mapping(
        capability.get("provenance"), f"plan.capabilities[{index}].provenance"
    )
    source = _require_string(
        provenance.get("source"), f"plan.capabilities[{index}].provenance.source"
    )
    digest = _require_string(
        provenance.get("digest"), f"plan.capabilities[{index}].provenance.digest"
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise CompilerError(
            f"plan.capabilities[{index}].provenance.digest must be a sha256 digest"
        )

    normalized: dict[str, Any] = {
        "name": name,
        "state": state,
        "kind": kind,
        "provenance": {"source": source, "digest": digest},
    }
    if state == "supported":
        argv = capability.get("argv")
        if not isinstance(argv, list) or not argv:
            raise CompilerError(f"plan.capabilities[{index}].argv must be non-empty")
        normalized["argv"] = [
            _require_string(token, f"plan.capabilities[{index}].argv[{token_index}]")
            for token_index, token in enumerate(argv)
        ]
    else:
        diagnostic = _require_string(
            capability.get("diagnostic"), f"plan.capabilities[{index}].diagnostic"
        )
        normalized["diagnostic"] = diagnostic
    return normalized


def load_plan(path: pathlib.Path) -> dict[str, Any]:
    """Load the authoritative V2 make-plan contract and normalize target order."""
    root = _load_json(path, "make plan")
    _validate_against_schema(root)
    if root.get("schema") != PLAN_SCHEMA:
        raise CompilerError(f"plan.schema must be {PLAN_SCHEMA}")
    repository_class = _require_string(
        root.get("repository_class"), "plan.repository_class"
    )
    provenance = _require_mapping(root.get("provenance"), "plan.provenance")
    producer = _require_string(provenance.get("producer"), "plan.provenance.producer")
    source = _require_string(provenance.get("source"), "plan.provenance.source")
    digest = _require_string(provenance.get("digest"), "plan.provenance.digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise CompilerError("plan.provenance.digest must be a sha256 digest")

    raw_capabilities = root.get("capabilities")
    if not isinstance(raw_capabilities, list):
        raise CompilerError("plan.capabilities must be an array")
    observed: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_capabilities):
        capability = _normalized_capability(raw, index)
        name = capability["name"]
        if name in observed:
            raise CompilerError(f"plan.capabilities contains duplicate name: {name}")
        observed[name] = capability

    expected = set(STANDARD_CAPABILITIES)
    if set(observed) != expected:
        missing = sorted(expected - set(observed))
        extra = sorted(set(observed) - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unapproved " + ", ".join(extra))
        raise CompilerError(
            "plan.capabilities must define the standard vocabulary: "
            + "; ".join(details)
        )

    return {
        "schema": PLAN_SCHEMA,
        "repository_class": repository_class,
        "provenance": {"producer": producer, "source": source, "digest": digest},
        "capabilities": [observed[name] for name in STANDARD_CAPABILITIES],
    }


def plan_digest(plan: Mapping[str, object]) -> str:
    """Return a canonical digest independent of input JSON whitespace or order."""
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _target_declarations(path: pathlib.Path) -> list[tuple[int, str]]:
    declarations: list[tuple[int, str]] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = MAKE_TARGET_PATTERN.match(line)
        if not match:
            continue
        declarations.extend((number, name) for name in match.group(1).split())
    return declarations


def validate_local_extensions(
    path: pathlib.Path, generated_targets: Sequence[str]
) -> None:
    """Reject protected local overrides; a missing optional extension is valid."""
    if not path.exists():
        return
    if not path.is_file():
        raise CompilerError(f"Repo.local.mk must be a regular file: {path}")
    forbidden = (
        set(generated_targets) | set(ROOT_TARGETS) | set(RESERVED_GOVERNANCE_TARGETS)
    )
    for number, target in _target_declarations(path):
        if target in forbidden:
            raise CompilerError(
                f"Repo.local.mk:{number} overrides protected target {target!r}"
            )


def _make_command(argv: Sequence[str]) -> str:
    rendered: list[str] = []
    for token in argv:
        if token == "@python":
            rendered.append("$(PYTHON)")
        else:
            rendered.append(shlex.quote(token))
    return " ".join(rendered)


def _shell_string(value: str) -> str:
    return shlex.quote(value)


def _render_header(plan: Mapping[str, object]) -> list[str]:
    provenance = cast(Mapping[str, object], plan["provenance"])
    try:
        template = REPO_MK_TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise CompilerError(
            f"Repo.mk template is missing: {REPO_MK_TEMPLATE_PATH}"
        ) from error
    try:
        return (
            template.format(
                plan_digest=plan_digest(plan),
                repository_class=plan["repository_class"],
                plan_source=provenance["source"],
            )
            .rstrip("\n")
            .splitlines()
        )
    except (KeyError, ValueError) as error:
        raise CompilerError(f"Repo.mk template is invalid: {error}") from error


def _target_list(values: Sequence[str]) -> list[str]:
    if not values:
        return []
    return [".PHONY: \\", "\t" + " \\\n\t".join(values), ""]


def render_repo_mk(plan: Mapping[str, object]) -> str:
    """Render the byte-stable generated adapter for an already validated plan."""
    capabilities = plan["capabilities"]
    assert isinstance(capabilities, list)
    supported = [item["name"] for item in capabilities if item["state"] == "supported"]
    not_required = [
        item["name"] for item in capabilities if item["state"] == "not_required"
    ]
    unsupported = [
        item["name"] for item in capabilities if item["state"] == "unsupported"
    ]
    generated = [
        "repo-capabilities",
        *(f"repo-{item['name']}" for item in capabilities),
    ]

    lines = _render_header(plan)
    lines.extend(
        [
            "PYTHON ?= python3",
            "",
            "L9_REPO_CLASS := " + str(plan["repository_class"]),
            "L9_REPO_CAPABILITY_PLAN_SHA256 := " + plan_digest(plan),
            "L9_REPO_SUPPORTED_CAPABILITIES := " + " ".join(supported),
            "L9_REPO_NOT_REQUIRED_CAPABILITIES := " + " ".join(not_required),
            "L9_REPO_UNSUPPORTED_CAPABILITIES := " + " ".join(unsupported),
            "L9_REPO_CAPABILITIES := "
            + " ".join(item["name"] for item in capabilities),
            "",
        ]
    )
    lines.extend(_target_list(generated))
    lines.extend(
        [
            "repo-capabilities:",
            "\t@printf '%-16s %-14s %s\\n' capability state provenance",
        ]
    )
    for capability in capabilities:
        lines.append(
            "\t@printf '%-16s %-14s %s\\n' "
            + " ".join(
                _shell_string(str(value))
                for value in (
                    capability["name"],
                    capability["state"],
                    capability["provenance"]["source"],
                )
            )
        )
    lines.append("")

    for capability in capabilities:
        name = str(capability["name"])
        state = str(capability["state"])
        lines.append(f"repo-{name}:")
        if state == "supported":
            lines.append("\t@" + _make_command(capability["argv"]))
        elif state == "not_required":
            lines.append(
                "\t@printf '%s\\n' "
                + _shell_string(f"NOT_REQUIRED: {name} - {capability['diagnostic']}")
            )
        else:
            lines.append(
                "\t@printf '%s\\n' "
                + _shell_string(f"UNSUPPORTED: {name} - {capability['diagnostic']}")
                + " >&2; exit 2"
            )
        lines.append("")
    return "\n".join(lines)


def _write_output(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_makefile_template() -> str:
    try:
        return MAKEFILE_TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise CompilerError(
            f"Makefile template is missing: {MAKEFILE_TEMPLATE_PATH}"
        ) from error


def _render_makefile(*outputs: pathlib.Path | None) -> None:
    paths = [output for output in outputs if output is not None]
    if not paths:
        return
    content = _load_makefile_template()
    for output in paths:
        _write_output(output, content)


def render(
    plan_path: pathlib.Path,
    output: pathlib.Path,
    local: pathlib.Path | None,
    makefile: pathlib.Path | None = None,
    legacy_makefile_template: pathlib.Path | None = None,
) -> None:
    plan = load_plan(plan_path)
    generated_targets = [
        "repo-capabilities",
        *(f"repo-{item['name']}" for item in plan["capabilities"]),
    ]
    if local is not None:
        validate_local_extensions(local, generated_targets)
    _write_output(output, render_repo_mk(plan))
    _render_makefile(makefile, legacy_makefile_template)
    print(f"rendered {output}")


def check(
    plan_path: pathlib.Path,
    output: pathlib.Path,
    local: pathlib.Path | None,
    makefile: pathlib.Path | None = None,
    legacy_makefile_template: pathlib.Path | None = None,
) -> None:
    plan = load_plan(plan_path)
    generated_targets = [
        "repo-capabilities",
        *(f"repo-{item['name']}" for item in plan["capabilities"]),
    ]
    if local is not None:
        validate_local_extensions(local, generated_targets)
    expected = render_repo_mk(plan)
    try:
        actual = output.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise CompilerError(f"generated output is missing: {output}") from error
    if actual != expected:
        raise CompilerError(f"generated output drifted: {output}; run make make-render")
    expected_makefile = _load_makefile_template()
    for makefile_artifact in (makefile, legacy_makefile_template):
        if makefile_artifact is None:
            continue
        try:
            actual_makefile = makefile_artifact.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise CompilerError(
                f"generated Makefile is missing: {makefile_artifact}"
            ) from error
        if actual_makefile != expected_makefile:
            raise CompilerError(
                f"generated Makefile drifted: {makefile_artifact}; run make reconcile"
            )
    print(f"verified {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("render", "check"):
        subparser = subcommands.add_parser(command)
        subparser.add_argument("--plan", type=pathlib.Path, required=True)
        subparser.add_argument("--output", type=pathlib.Path, required=True)
        subparser.add_argument("--local", type=pathlib.Path)
        subparser.add_argument("--makefile", type=pathlib.Path)
        subparser.add_argument("--legacy-makefile-template", type=pathlib.Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "render":
            render(
                args.plan,
                args.output,
                args.local,
                args.makefile,
                args.legacy_makefile_template,
            )
        else:
            check(
                args.plan,
                args.output,
                args.local,
                args.makefile,
                args.legacy_makefile_template,
            )
    except CompilerError as error:
        print(f"l9_make: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
