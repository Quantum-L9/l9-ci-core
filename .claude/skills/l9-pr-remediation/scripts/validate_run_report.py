#!/usr/bin/env python3
"""Validate a PR-remediation run report against the canonical schema, fail-closed.

A remediation run is trustworthy only if the report it emits proves the runtime
gates (A-G) were honored. This validator checks an emitted run report against
``schemas/run-report.schema.json`` (JSON Schema Draft 2020-12) when ``jsonschema``
is available, and ALWAYS enforces the cross-field hard invariants the gates
promise -- which a plain schema cannot express -- in code.

It degrades gracefully: if ``jsonschema`` (or the schema file) is unavailable,
it falls back to a structural required-key check plus the same invariants, so
the validator never hard-crashes and the invariant gates always run.

Exit codes: 0 pass, 1 validation failure, 2 unreadable/unparseable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_NAME = "run-report.schema.json"

# Top-level required sections (fallback structural check mirrors the schema).
REQUIRED_TOP = ["schema_version", "run", "gates", "findings", "convergence", "summary"]
REQUIRED_GATES = [
    "gate_registry",
    "classified_findings",
    "local_verify_log",
    "push_record",
    "reply_record",
    "report_record",
]
CONVERGENCE_STATES = {"converged", "partial", "blocked"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_path() -> Path:
    # scripts/ -> pack root -> schemas/<name>
    return Path(__file__).resolve().parent.parent / "schemas" / SCHEMA_NAME


def _schema_errors(report: dict[str, Any]) -> list[str]:
    """Full JSON-Schema validation when jsonschema + schema file are present.

    Returns an empty list when the library/schema is unavailable so the caller
    falls back to the structural check; genuine schema violations are returned
    as ``schema: ...`` messages.
    """
    try:
        import jsonschema  # type: ignore[import-not-found]
    except Exception:
        return []
    schema_file = _schema_path()
    if not schema_file.exists():
        return []
    try:
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
    except Exception as exc:  # malformed schema -> fall back, do not crash
        return [f"schema loader unavailable: {exc}"]
    errors = []
    for err in sorted(validator.iter_errors(report), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"schema: {loc}: {err.message}")
    return errors


def _structural_errors(report: dict[str, Any]) -> list[str]:
    """Required-key check used when full schema validation did not run."""
    errors = []
    for key in REQUIRED_TOP:
        if key not in report:
            errors.append(f"structure: missing top-level section: {key}")
    gates = report.get("gates")
    if isinstance(gates, dict):
        for key in REQUIRED_GATES:
            if key not in gates:
                errors.append(f"structure: gates.{key} missing")
    elif "gates" in report:
        errors.append("structure: gates must be a mapping")
    return errors


def _num(node: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _invariant_errors(report: dict[str, Any]) -> list[str]:
    """Cross-field hard invariants the gates promise (schema cannot express these)."""
    errors = []
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}

    push_count = _num(gates, "push_record", "push_count_this_cycle")
    if push_count != 1:
        errors.append(f"invariant: push_record.push_count_this_cycle must be 1, got {push_count!r}")

    lv = gates.get("local_verify_log") if isinstance(gates.get("local_verify_log"), dict) else {}
    if lv.get("all_green") is not True:
        errors.append("invariant: local_verify_log.all_green must be true before a push")
    gates_run = lv.get("gates_run")
    total_gates = _num(gates, "gate_registry", "total_gates")
    if isinstance(gates_run, int) and isinstance(total_gates, int) and gates_run != total_gates:
        errors.append(
            f"invariant: local_verify_log.gates_run ({gates_run}) != "
            f"gate_registry.total_gates ({total_gates})"
        )

    reply = gates.get("reply_record") if isinstance(gates.get("reply_record"), dict) else {}
    replied = reply.get("threads_replied")
    total_threads = reply.get("threads_total")
    if isinstance(replied, int) and isinstance(total_threads, int) and replied != total_threads:
        errors.append(
            f"invariant: reply_record.threads_replied ({replied}) != "
            f"threads_total ({total_threads}) -- silent-fix"
        )

    findings = report.get("findings") if isinstance(report.get("findings"), dict) else {}
    for bucket in ("rejected", "deferred"):
        for i, item in enumerate(findings.get(bucket, []) or []):
            if not isinstance(item, dict) or not str(item.get("reason", "")).strip():
                errors.append(f"invariant: findings.{bucket}[{i}] must carry a non-empty reason")

    status = _num(report, "convergence", "convergence_status")
    if status not in CONVERGENCE_STATES:
        errors.append(
            f"invariant: convergence.convergence_status must be one of "
            f"{sorted(CONVERGENCE_STATES)}, got {status!r}"
        )
    return errors


def validate(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["structure: run report root must be a JSON object"]
    schema_errors = _schema_errors(report)
    # Only add structural checks when full schema validation did not run.
    structural = [] if schema_errors else _structural_errors(report)
    return schema_errors + structural + _invariant_errors(report)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a PR-remediation run report.")
    parser.add_argument("run_report", help="Path to an emitted run-report JSON file")
    args = parser.parse_args()
    path = Path(args.run_report)
    if not path.exists() or not path.is_file():
        print(f"FAIL: not a file: {path}", file=sys.stderr)
        return 2
    try:
        report = _load_json(path)
    except Exception as exc:
        print(f"FAIL: cannot parse {path.name} as JSON: {exc}", file=sys.stderr)
        return 2
    errors = validate(report)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: run report validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
