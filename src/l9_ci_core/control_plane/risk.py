"""Deterministic risk classification.

Classifies a changed-file set (plus event labels) into a risk tier using
``.github/governance/risk-tiers.yaml`` (validated against
``schemas/risk-tiers.schema.json``).

Invariants (PR §6.4):

* the highest matching tier wins;
* a label may raise risk but never lower path-derived risk;
* an unknown diff yields the configured high-risk fail-closed tier;
* malformed changed-file input yields high risk plus a control-plane warning;
* an empty push diff is not treated as "no change" — the changed-file
  collector marks such failures ``unknown_diff`` and this classifier honors it;
* output is deterministic for fixed inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import schemas
from .digests import policy_digests
from .models import ChangedFiles

__all__ = [
    "RiskError",
    "RiskPolicy",
    "RiskResult",
    "load_risk_policy",
    "classify",
    "classify_changed_files",
]


class RiskError(ValueError):
    """Raised when the risk-tier policy is invalid."""


@lru_cache(maxsize=2048)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile a gitignore-ish path glob.

    ``**`` matches across directory separators, ``*`` matches within a single
    path segment, ``?`` matches one non-separator character.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                j = i + 2
                if j < n and pattern[j] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                else:
                    out.append(".*")
                    i = j
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + r"\Z")


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_compile_glob(p).match(path) for p in patterns)


def _first_hit(paths: list[str], patterns: list[str]) -> str | None:
    for p in paths:
        if _matches_any(p, patterns):
            return p
    return None


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    default_tier: str
    fallback: str
    tiers: dict[str, int]  # tier -> rank
    classification: dict[str, dict[str, Any]]
    precedence: tuple[str, ...]
    label_may_raise: bool
    label_may_lower: bool
    unknown_tier: str
    unknown_reason: str
    path: str | None = None
    source_digest: str | None = None
    semantic_digest: str | None = None

    def rank(self, tier: str) -> int:
        return self.tiers[tier]


@dataclass(frozen=True, slots=True)
class RiskResult:
    risk_tier: str
    rank: int
    reasons: list[str] = field(default_factory=list)
    unknown_diff: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_tier": self.risk_tier,
            "rank": self.rank,
            "reasons": list(self.reasons),
            "unknown_diff": self.unknown_diff,
        }


def _build_policy(data: Any, **meta: Any) -> RiskPolicy:
    return RiskPolicy(
        default_tier=data["default_tier"],
        fallback=data["fallback"],
        tiers={name: int(spec["rank"]) for name, spec in data["tiers"].items()},
        classification=data["classification"],
        precedence=tuple(data["precedence"]),
        label_may_raise=bool(data["label_rules"]["label_may_raise"]),
        label_may_lower=bool(data["label_rules"]["label_may_lower"]),
        unknown_tier=data["unknown_diff_behavior"]["risk_tier"],
        unknown_reason=data["unknown_diff_behavior"]["classification_reason"],
        **meta,
    )


def load_risk_policy(path: str | Path) -> RiskPolicy:
    """Load, digest, and validate the risk-tier policy at ``path``."""
    p = Path(path)
    if not p.is_file():
        raise RiskError(f"risk policy not found: {p}")
    src, sem, parsed = policy_digests(p)
    errors = schemas.iter_errors("risk-tiers", parsed)
    if errors:
        joined = "; ".join(schemas.format_error(e) for e in errors)
        raise RiskError(f"risk-tiers schema invalid: {joined}")
    return _build_policy(parsed, path=str(path), source_digest=src, semantic_digest=sem)


def _path_tier(policy: RiskPolicy, files: list[str]) -> tuple[str, list[str]]:
    matched: dict[str, str] = {}
    cls = policy.classification

    for tier in ("regulated", "high"):
        rule = cls.get(tier, {})
        hit = _first_hit(files, rule.get("any_paths", []))
        if hit is not None:
            matched[tier] = f"path {hit!r} matched {tier} rule"

    low = cls.get("low")
    if low is not None and files:
        all_paths = low.get("all_paths", [])
        forbidden = low.get("forbidden_paths", [])
        covered = all(_matches_any(f, all_paths) for f in files)
        forbidden_hit = _first_hit(files, forbidden)
        if covered and forbidden_hit is None:
            matched["low"] = "all changed paths are documentation/metadata"

    if matched:
        tier = max(matched, key=policy.rank)
        return tier, [matched[tier]]
    return policy.fallback, [
        f"no classification rule matched {len(files)} changed file(s); "
        f"fallback to {policy.fallback}"
    ]


def _label_tier(policy: RiskPolicy, labels: list[str]) -> tuple[str | None, str | None]:
    matched: dict[str, str] = {}
    for tier, rule in policy.classification.items():
        allowed = rule.get("any_labels", [])
        hit = next((lbl for lbl in labels if lbl in allowed), None)
        if hit is not None:
            matched[tier] = f"label {hit!r} matched {tier} rule"
    if matched:
        tier = max(matched, key=policy.rank)
        return tier, matched[tier]
    return None, None


def classify(
    policy: RiskPolicy,
    files: list[str],
    labels: list[str] | None = None,
    *,
    unknown_diff: bool = False,
    warnings: list[str] | None = None,
) -> RiskResult:
    """Classify a changed-file set (+ labels) into a risk tier."""
    labels = list(labels or [])
    warns = list(warnings or [])

    if unknown_diff:
        return RiskResult(
            risk_tier=policy.unknown_tier,
            rank=policy.rank(policy.unknown_tier),
            reasons=[policy.unknown_reason],
            unknown_diff=True,
            warnings=warns,
        )

    tier, reasons = _path_tier(policy, files)
    label_tier, label_reason = _label_tier(policy, labels)

    if label_tier is not None and label_reason is not None:
        if policy.label_may_raise and policy.rank(label_tier) > policy.rank(tier):
            tier = label_tier
            reasons = [*reasons, f"{label_reason} raised risk"]
        elif policy.rank(label_tier) < policy.rank(tier) and not policy.label_may_lower:
            reasons = [*reasons, f"{label_reason} ignored (labels may not lower risk)"]

    return RiskResult(
        risk_tier=tier,
        rank=policy.rank(tier),
        reasons=reasons,
        unknown_diff=False,
        warnings=warns,
    )


def classify_changed_files(
    policy: RiskPolicy,
    changed: ChangedFiles | dict[str, Any],
    labels: list[str] | None = None,
) -> RiskResult:
    """Classify from a changed-files payload.

    Malformed input fails closed to the unknown-diff (high) tier plus a
    control-plane warning, rather than raising.
    """
    if isinstance(changed, ChangedFiles):
        data = changed.to_dict()
    else:
        data = changed
    errors = schemas.iter_errors("changed-files", data)
    if errors:
        warn = "malformed changed-files input: " + "; ".join(
            schemas.format_error(e) for e in errors[:3]
        )
        return classify(policy, [], labels, unknown_diff=True, warnings=[warn])
    return classify(
        policy,
        list(data.get("files", [])),
        labels,
        unknown_diff=bool(data.get("unknown_diff", False)),
    )
