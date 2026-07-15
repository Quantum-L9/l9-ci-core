#!/usr/bin/env python3
"""Classify changed files into one canonical L9 CI PR class from governance policy.

Policy source of truth:
- .github/governance/l9-ci-shared-spec.yaml

Input sources, in order:
1. CLI file paths
2. CHANGED_FILES environment variable, newline or comma separated
3. stdin, newline separated

Output is JSON by default. Use --plain to print only the class.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(".github/governance/l9-ci-shared-spec.yaml")
SCRIPT_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "governance" / "l9-ci-shared-spec.yaml"


@dataclass(frozen=True)
class ClassifierPolicy:
    canonical_classes: set[str]
    unknown_class: str
    docs_only_class: str
    priority: list[str]
    taxonomy: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Classification:
    pr_class: str
    changed_files: list[str]
    reasons: list[str]
    matched_tags: dict[str, list[str]]


def _norm(path: str) -> str:
    value = path.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _split_env(value: str) -> list[str]:
    raw: list[str] = []
    for line in value.replace(",", "\n").splitlines():
        item = _norm(line)
        if item:
            raw.append(item)
    return raw


def collect_files(argv_files: list[str]) -> list[str]:
    files = [_norm(p) for p in argv_files if _norm(p)]
    if files:
        return sorted(dict.fromkeys(files))
    env_value = os.environ.get("CHANGED_FILES", "")
    if env_value.strip():
        return sorted(dict.fromkeys(_split_env(env_value)))
    if not sys.stdin.isatty():
        stdin_value = sys.stdin.read()
        if stdin_value.strip():
            return sorted(dict.fromkeys(_split_env(stdin_value)))
    return []


def _default_config_path() -> Path:
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return SCRIPT_DEFAULT_CONFIG_PATH


def load_policy(config_path: Path | None = None) -> ClassifierPolicy:
    path = config_path or _default_config_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"classifier policy not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SystemExit(f"classifier policy is malformed YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"classifier policy must be a mapping: {path}")
    classifier = raw.get("classifier")
    if not isinstance(classifier, dict):
        raise SystemExit(f"classifier policy missing 'classifier' mapping: {path}")
    canonical = classifier.get("canonical_classes")
    priority = classifier.get("priority")
    taxonomy = classifier.get("taxonomy")
    unknown_class = classifier.get("unknown_class", "unknown_diff")
    docs_only_class = classifier.get("docs_only_class", "docs_only")
    if not isinstance(canonical, list) or not all(isinstance(v, str) for v in canonical):
        raise SystemExit("classifier.canonical_classes must be a list of strings")
    if not isinstance(priority, list) or not all(isinstance(v, str) for v in priority):
        raise SystemExit("classifier.priority must be a list of strings")
    if not isinstance(taxonomy, dict):
        raise SystemExit("classifier.taxonomy must be a mapping")
    canonical_set = set(canonical)
    if unknown_class not in canonical_set:
        raise SystemExit("classifier.unknown_class must be canonical")
    if docs_only_class not in canonical_set:
        raise SystemExit("classifier.docs_only_class must be canonical")
    for cls in priority:
        if cls not in canonical_set:
            raise SystemExit(f"classifier.priority contains non-canonical class: {cls}")
    for group, rule in taxonomy.items():
        if not isinstance(rule, dict):
            raise SystemExit(f"classifier.taxonomy.{group} must be a mapping")
        cls = rule.get("class")
        if not isinstance(cls, str) or cls not in canonical_set:
            raise SystemExit(f"classifier.taxonomy.{group}.class must be canonical")
    return ClassifierPolicy(
        canonical_classes=canonical_set,
        unknown_class=unknown_class,
        docs_only_class=docs_only_class,
        priority=priority,
        taxonomy=taxonomy,
    )


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [str(item).lower() for item in value]


def _parts(lower: str) -> set[str]:
    return set(lower.replace("-", "_").replace(".", "/").split("/"))


def _reason(rule: dict[str, Any], key: str, fallback: str) -> str:
    reasons = rule.get("reasons")
    if isinstance(reasons, dict) and isinstance(reasons.get(key), str):
        return str(reasons[key])
    return fallback


def _matches_prefix(lower: str, rule: dict[str, Any]) -> str | None:
    for prefix in _as_str_list(rule.get("prefixes")):
        if lower.startswith(prefix):
            return _reason(rule, "prefix", f"matched prefix {prefix}")
    return None


def _matches_suffix(lower: str, rule: dict[str, Any]) -> str | None:
    suffix = Path(lower).suffix
    for expected in _as_str_list(rule.get("suffixes")):
        if suffix == expected:
            return _reason(rule, "suffix", f"matched suffix {expected}")
    return None


def _matches_exact(lower: str, name: str, rule: dict[str, Any]) -> str | None:
    exact = set(_as_str_list(rule.get("exact")))
    if lower in exact or name in exact:
        return _reason(rule, "exact", "matched exact file name")
    return None


def _matches_contains(lower: str, rule: dict[str, Any]) -> str | None:
    for token in _as_str_list(rule.get("contains")):
        if token in lower:
            return _reason(rule, "contains", f"matched token {token}")
    return None


def _matches_parts(parts: set[str], rule: dict[str, Any]) -> str | None:
    if parts.intersection(set(_as_str_list(rule.get("parts")))):
        return _reason(rule, "part", "matched path segment")
    return None


def _matches_name_pattern(name: str, rule: dict[str, Any]) -> str | None:
    raw_patterns = rule.get("name_prefix_suffix") or []
    if not isinstance(raw_patterns, list):
        return None
    for pattern in raw_patterns:
        if not isinstance(pattern, dict):
            continue
        prefix = str(pattern.get("prefix", "")).lower()
        suffix = str(pattern.get("suffix", "")).lower()
        if name.startswith(prefix) and name.endswith(suffix):
            return _reason(rule, "pattern", f"matched name pattern {prefix}*{suffix}")
    return None


def _tags_for(path: str, policy: ClassifierPolicy) -> list[tuple[str, str]]:
    lower = path.lower()
    name = Path(lower).name
    parts = _parts(lower)
    tags: list[tuple[str, str]] = []
    for group_name, rule in policy.taxonomy.items():
        cls = str(rule["class"])
        match_reason = (
            _matches_prefix(lower, rule)
            or _matches_suffix(lower, rule)
            or _matches_exact(lower, name, rule)
            or _matches_contains(lower, rule)
            or _matches_parts(parts, rule)
            or _matches_name_pattern(name, rule)
        )
        if match_reason:
            tags.append((cls, f"{path}: {match_reason} ({group_name})"))
    return tags


def classify(paths: list[str], policy: ClassifierPolicy | None = None) -> Classification:
    active_policy = policy or load_policy()
    changed = sorted(dict.fromkeys(_norm(p) for p in paths if _norm(p)))
    if not changed:
        return Classification(active_policy.unknown_class, [], ["no changed files supplied"], {})

    observed: dict[str, list[str]] = {}
    unknown: list[str] = []
    for path in changed:
        tags = _tags_for(path, active_policy)
        if not tags:
            unknown.append(path)
            continue
        for cls, reason in tags:
            observed.setdefault(cls, []).append(reason)

    if unknown:
        return Classification(
            active_policy.unknown_class,
            changed,
            [f"unclassified path: {p}" for p in unknown],
            observed,
        )

    if set(observed) == {active_policy.docs_only_class}:
        return Classification(
            active_policy.docs_only_class,
            changed,
            observed[active_policy.docs_only_class],
            observed,
        )

    for cls in active_policy.priority:
        if cls in observed and cls != active_policy.docs_only_class:
            return Classification(cls, changed, observed[cls], observed)

    return Classification(active_policy.unknown_class, changed, ["no canonical class matched"], observed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify changed files into one canonical L9 CI PR class.")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--config", type=Path, help="Classifier policy YAML path.")
    parser.add_argument("--plain", action="store_true", help="Print only the canonical class.")
    args = parser.parse_args(argv)

    policy = load_policy(args.config)
    result = classify(collect_files(args.files), policy)
    if result.pr_class not in policy.canonical_classes:
        print(f"invalid classifier output: {result.pr_class}", file=sys.stderr)
        return 2
    if args.plain:
        print(result.pr_class)
    else:
        print(
            json.dumps(
                {
                    "pr_class": result.pr_class,
                    "changed_files": result.changed_files,
                    "reasons": result.reasons,
                    "matched_tags": result.matched_tags,
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
