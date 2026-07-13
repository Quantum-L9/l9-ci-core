#!/usr/bin/env python3
"""Validate a SMART exemplary spec YAML for behavioral intelligence gates.

Uses only the Python standard library plus PyYAML when available. If PyYAML is
not installed, the script falls back to textual gate checks so it can still run
inside lean skill environments.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REQUIRED_GATE_KEYS = [
    "activation_precision",
    "adapter_architecture",
    "evidence_hierarchy",
    "doctrine_extraction",
    "expert_heuristics",
    "failure_modes",
    "leverage_model",
    "self_improvement_hook",
    "compiler_enforcement_gates",
    "skill_intelligence_report",
]

MAX_COUNTS = {
    "strong_signals": 5,
    "weak_signals": 5,
    "reject_signals": 5,
    "authority_order": 7,
    "expert_heuristics": 7,
    "adapters": 3,
    "failure_modes": 5,
    "experts": 5,
    "doctrine": 10,
    "invariants": 10,
    "authority_hierarchy": 5,
    "activation_signals": 5,
    "leverage_points": 5,
}


def _load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore[import-not-found]
    except Exception:
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("spec root must be a mapping")
    return data


def _count_list(node: Any) -> int:
    return len(node) if isinstance(node, list) else 0


def _validate_structured(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    spec = data.get("smart_exemplary_spec")
    if not isinstance(spec, dict):
        return ["missing smart_exemplary_spec root"]

    pipeline = spec.get("compiler_pipeline", {})
    if isinstance(pipeline, dict):
        order = pipeline.get("required_order", [])
        required_order = ["parse_source", "extract_expertise", "compress_expertise", "design_skill", "run_exemplary_gate", "package"]
        if order != required_order:
            errors.append("compiler_pipeline.required_order must match exemplary pipeline")
    else:
        errors.append("compiler_pipeline is required")

    expertise = spec.get("expertise_model", {})
    if isinstance(expertise, dict):
        for key in ("experts", "doctrine", "invariants", "authority_hierarchy", "activation_signals", "reject_signals", "adapters", "failure_modes", "leverage_points"):
            node = expertise.get(key, {})
            if not isinstance(node, dict):
                errors.append(f"expertise_model.{key} must be a mapping")
                continue
            if node.get("required") is not True:
                errors.append(f"expertise_model.{key}.required must be true")
            items = node.get("items", [])
            max_count = node.get("max", MAX_COUNTS.get(key))
            if isinstance(items, list) and isinstance(max_count, int) and len(items) > max_count:
                errors.append(f"expertise_model.{key}.items exceeds max {max_count}: {len(items)}")
    else:
        errors.append("expertise_model is required")

    activation = spec.get("activation_model", {})
    if not isinstance(activation, dict):
        errors.append("activation_model must be a mapping")
        activation = {}
    for key in ("strong_signals", "weak_signals", "reject_signals"):
        count = _count_list(activation.get(key))
        if count > MAX_COUNTS[key]:
            errors.append(f"{key} exceeds max {MAX_COUNTS[key]}: {count}")
    # Canonical templates may leave lists empty; compiled skill validation enforces populated reject signals.

    authority_count = _count_list(spec.get("authority_order"))
    if authority_count == 0:
        errors.append("authority_order is required")
    if authority_count > MAX_COUNTS["authority_order"]:
        errors.append(f"authority_order exceeds max {MAX_COUNTS['authority_order']}: {authority_count}")

    heuristic_node = spec.get("expert_heuristics")
    heuristic_items = heuristic_node.get("items", []) if isinstance(heuristic_node, dict) else heuristic_node
    heuristic_count = _count_list(heuristic_items)
    if heuristic_node is None:
        errors.append("expert_heuristics are required")
    if heuristic_count > MAX_COUNTS["expert_heuristics"]:
        errors.append(f"expert_heuristics exceeds max {MAX_COUNTS['expert_heuristics']}: {heuristic_count}")

    adapter_map = spec.get("adapter_map", {})
    adapters = adapter_map.get("adapters", []) if isinstance(adapter_map, dict) else []
    if _count_list(adapters) > MAX_COUNTS["adapters"]:
        errors.append(f"adapters exceeds max {MAX_COUNTS['adapters']}: {_count_list(adapters)}")
    if isinstance(adapters, list):
        for index, adapter in enumerate(adapters, start=1):
            if not isinstance(adapter, dict):
                errors.append(f"adapter {index} must be a mapping")
                continue
            if not adapter.get("load_when"):
                errors.append(f"adapter {index} missing load_when")
            if not adapter.get("changes"):
                errors.append(f"adapter {index} missing changes")

    failure_node = spec.get("failure_modes")
    failure_items = failure_node.get("items", []) if isinstance(failure_node, dict) else failure_node
    failure_count = _count_list(failure_items)
    if failure_node is None:
        errors.append("failure_modes are required")
    if failure_count > MAX_COUNTS["failure_modes"]:
        errors.append(f"failure_modes exceeds max {MAX_COUNTS['failure_modes']}: {failure_count}")

    gates = spec.get("exemplary_gate", {})
    if not isinstance(gates, dict):
        errors.append("exemplary_gate must be a mapping")
        gates = {}
    for key in REQUIRED_GATE_KEYS:
        if key not in gates:
            errors.append(f"exemplary_gate missing {key}")

    return errors


def _validate_text(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    required_terms = [
        "smart_exemplary_spec",
        "activation_model",
        "reject_signals",
        "authority_order",
        "expert_heuristics",
        "adapter_map",
        "failure_modes",
        "self_improvement_hook",
        "exemplary_gate",
    ]
    return [f"missing required term: {term}" for term in required_terms if term not in text]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a SMART exemplary skill spec YAML file.")
    parser.add_argument("path", help="Path to canonical or generated SMART exemplary spec YAML")
    args = parser.parse_args()
    path = Path(args.path)
    if not path.exists():
        print(f"FAIL: file not found: {path}", file=sys.stderr)
        return 2

    try:
        data = _load_yaml(path)
        errors = _validate_structured(data) if data is not None else _validate_text(path)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: SMART exemplary spec structure is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
