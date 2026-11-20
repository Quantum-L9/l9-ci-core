#!/usr/bin/env python3
"""Core's supply-chain lane: one mechanism, per-repository policy data.

Subcommands (stdlib only; run with ``python3 -I``):

``changes``
    Whether a pull request changes a dependency manifest or lock file. Writes
    ``run=true|false`` to ``$GITHUB_OUTPUT``. Any other event runs.

``policy``
    Merge Core's ``default-policy.json`` with the repository's optional
    ``.l9/supply-chain-policy.json`` and write the dependency-review inputs
    (``allow-licenses``, ``allow-dependencies-licenses``,
    ``fail-on-severity``) to ``$GITHUB_OUTPUT``. A repository may add allowed
    licences and exempt named packages; it can never allow a denied licence,
    and a malformed policy fails.

``evaluate``
    Judge a CycloneDX JSON SBOM against the merged policy. A component whose
    licence is denied, or known but not allowed, fails the run; a component
    whose licence cannot be determined is reported, not failed (as
    dependency review treats it). An SBOM with no components fails closed:
    an empty inventory proves nothing.

Exit codes: 0 pass, 1 policy violation, 2 invalid input or policy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_POLICY = HERE / "default-policy.json"
REPO_POLICY = Path(".l9") / "supply-chain-policy.json"
POLICY_SCHEMA = "l9.supply-chain-policy/v1"
REPO_POLICY_SCHEMA = "l9.supply-chain-repo-policy/v1"
SEVERITIES = ("low", "moderate", "high", "critical")
PURL = re.compile(r"^pkg:[a-z0-9.+-]+/[^\s,]+$")

# Manifests and lock files whose change makes a pull request a dependency
# change. Matched on the file name, anywhere in the tree.
DEPENDENCY_FILES = re.compile(
    r"(^|/)("
    r"pyproject\.toml|uv\.lock|poetry\.lock|Pipfile(\.lock)?|setup\.(py|cfg)"
    r"|[^/]*requirements[^/]*\.(txt|in)|constraints[^/]*\.txt"
    r"|package\.json|package-lock\.json|npm-shrinkwrap\.json|yarn\.lock"
    r"|pnpm-lock\.yaml|go\.mod|go\.sum|Cargo\.toml|Cargo\.lock"
    r"|\.l9/supply-chain-policy\.json"
    r")$"
)


class PolicyError(ValueError):
    """The default or repository policy is unreadable or malformed."""


@dataclass(frozen=True)
class Policy:
    fail_on_severity: str
    allowed: frozenset[str]
    denied: frozenset[str]
    aliases: dict[str, str]
    exempt: tuple[str, ...] = field(default_factory=tuple)


def _write_output(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT", "")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if target:
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(lines)
    else:
        sys.stdout.write(lines)


def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PolicyError(f"{what} unreadable: {error}") from error


def _strings(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"{what} must be a list of strings")
    return list(value)


def load_policy(workspace: Path, default: Path = DEFAULT_POLICY) -> Policy:
    base = _read_json(default, "Core supply-chain policy")
    if not isinstance(base, dict) or base.get("schema") != POLICY_SCHEMA:
        raise PolicyError("Core supply-chain policy has the wrong schema")
    allowed = set(_strings(base.get("allowed_licenses"), "allowed_licenses"))
    denied = set(_strings(base.get("denied_licenses"), "denied_licenses"))
    aliases = base.get("license_aliases")
    if not isinstance(aliases, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()
    ):
        raise PolicyError("license_aliases must map strings to strings")
    severity = base.get("fail_on_severity")
    if severity not in SEVERITIES:
        raise PolicyError(f"fail_on_severity must be one of {SEVERITIES}")
    exempt: list[str] = []
    repo_path = workspace / REPO_POLICY
    if repo_path.is_symlink():
        raise PolicyError(f"{REPO_POLICY} must not be a symlink")
    if repo_path.is_file():
        repo = _read_json(repo_path, str(REPO_POLICY))
        if not isinstance(repo, dict) or repo.get("schema") != REPO_POLICY_SCHEMA:
            raise PolicyError(f"{REPO_POLICY} must declare schema {REPO_POLICY_SCHEMA}")
        unknown = set(repo) - {
            "schema",
            "additional_allowed_licenses",
            "allow_dependencies_licenses",
        }
        if unknown:
            raise PolicyError(f"{REPO_POLICY} has unknown keys: {sorted(unknown)}")
        extra = set(
            _strings(
                repo.get("additional_allowed_licenses", []),
                "additional_allowed_licenses",
            )
        )
        conflict = extra & denied
        if conflict:
            raise PolicyError(
                f"{REPO_POLICY} cannot allow a Core-denied licence: {sorted(conflict)}"
            )
        allowed |= extra
        exempt = _strings(
            repo.get("allow_dependencies_licenses", []), "allow_dependencies_licenses"
        )
        bad = [p for p in exempt if not PURL.match(p)]
        if bad:
            raise PolicyError(
                f"allow_dependencies_licenses entries must be purls: {bad}"
            )
    return Policy(
        fail_on_severity=str(severity),
        allowed=frozenset(allowed - denied),
        denied=frozenset(denied),
        aliases=dict(aliases),
        exempt=tuple(sorted(set(exempt))),
    )


# --- licence expressions --------------------------------------------------

_TOKEN = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[^\s()]+")


def _tokens(expression: str) -> list[str]:
    return _TOKEN.findall(expression)


def _parse(tokens: list[str]) -> Any:
    """SPDX expression -> nested ("OR"|"AND", [...]) or a licence id string."""

    def expr(pos: int) -> tuple[Any, int]:
        left, pos = term(pos)
        terms = [left]
        while pos < len(tokens) and tokens[pos] == "OR":
            right, pos = term(pos + 1)
            terms.append(right)
        return (("OR", terms) if len(terms) > 1 else left), pos

    def term(pos: int) -> tuple[Any, int]:
        left, pos = atom(pos)
        factors = [left]
        while pos < len(tokens) and tokens[pos] == "AND":
            right, pos = atom(pos + 1)
            factors.append(right)
        return (("AND", factors) if len(factors) > 1 else left), pos

    def atom(pos: int) -> tuple[Any, int]:
        if pos >= len(tokens):
            raise ValueError("unexpected end of expression")
        token = tokens[pos]
        if token == "(":
            inner, pos = expr(pos + 1)
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ValueError("unbalanced parenthesis")
            return inner, pos + 1
        if token in (")", "AND", "OR", "WITH"):
            raise ValueError(f"unexpected {token}")
        pos += 1
        if pos < len(tokens) and tokens[pos] == "WITH":
            # An exception narrows the licence it modifies; judge the licence.
            pos += 2
        return token, pos

    tree, end = expr(0)
    if end != len(tokens):
        raise ValueError("trailing tokens")
    return tree


def _verdict(tree: Any, policy: Policy) -> str:
    """ "allowed", "denied" or "not_allowed" for one parsed expression."""
    if isinstance(tree, str):
        if tree in policy.denied:
            return "denied"
        return "allowed" if tree in policy.allowed else "not_allowed"
    operator, parts = tree
    verdicts = [_verdict(part, policy) for part in parts]
    if operator == "AND":
        for worst in ("denied", "not_allowed"):
            if worst in verdicts:
                return worst
        return "allowed"
    if "allowed" in verdicts:  # OR: the consumer may choose an allowed branch
        return "allowed"
    return "denied" if all(v == "denied" for v in verdicts) else "not_allowed"


def classify_licences(entries: Iterable[str], policy: Policy) -> tuple[str, str]:
    """(verdict, rendered) for a component's licence entries (ANDed together).

    The verdict is "allowed", "denied", "not_allowed" or "unknown".
    """
    rendered: list[str] = []
    verdicts: list[str] = []
    for raw in entries:
        text = policy.aliases.get(raw.strip(), raw.strip())
        if not text or text in ("NOASSERTION", "NONE", "UNKNOWN"):
            continue
        rendered.append(text)
        try:
            verdicts.append(_verdict(_parse(_tokens(text)), policy))
        except ValueError:
            verdicts.append("not_allowed" if " " not in text else "unknown")
    if not verdicts:
        return "unknown", ""
    for worst in ("denied", "not_allowed", "unknown"):
        if worst in verdicts:
            return worst, " AND ".join(rendered)
    return "allowed", " AND ".join(rendered)


def _component_licences(component: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("expression"), str):
            found.append(entry["expression"])
            continue
        licence = entry.get("license")
        if isinstance(licence, dict):
            for key in ("id", "name"):
                if isinstance(licence.get(key), str):
                    found.append(licence[key])
                    break
    return found


def evaluate(sbom: dict[str, Any], policy: Policy) -> dict[str, Any]:
    components = sbom.get("components")
    if not isinstance(components, list):
        raise PolicyError("SBOM has no components list")
    rows: list[dict[str, str]] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("type") not in (None, "library", "framework"):
            continue
        purl = str(component.get("purl") or "")
        base_purl = purl.split("@", 1)[0].split("?", 1)[0]
        verdict, rendered = classify_licences(_component_licences(component), policy)
        if verdict != "allowed" and base_purl in policy.exempt:
            verdict = "exempt"
        rows.append(
            {
                "name": str(component.get("name") or "?"),
                "version": str(component.get("version") or "?"),
                "purl": purl,
                "licence": rendered,
                "verdict": verdict,
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    if not rows:
        status = "empty"
    elif counts.get("denied") or counts.get("not_allowed"):
        status = "fail"
    else:
        status = "pass"
    return {
        "schema": "l9.supply-chain-licences/v1",
        "status": status,
        "counts": dict(sorted(counts.items())),
        "components": sorted(rows, key=lambda r: (r["verdict"], r["name"])),
    }


# --- commands -----------------------------------------------------------------


def _cmd_changes(args: argparse.Namespace) -> int:
    event = os.environ.get("EVENT_NAME", "")
    if event not in ("pull_request", "pull_request_target"):
        _write_output({"run": "true", "reason": f"event {event or 'unknown'}"})
        return 0
    base, head = os.environ.get("BASE_SHA", ""), os.environ.get("HEAD_SHA", "")
    completed = subprocess.run(
        ["git", "-C", str(args.workspace), "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not base or not head:
        # Undeterminable change set: run rather than skip.
        _write_output({"run": "true", "reason": "change set unreadable"})
        print(
            f"::warning::cannot list changed files; running: {completed.stderr.strip()}"
        )
        return 0
    changed = [p for p in completed.stdout.splitlines() if DEPENDENCY_FILES.search(p)]
    _write_output(
        {
            "run": "true" if changed else "false",
            "reason": ", ".join(changed[:10])
            if changed
            else "no dependency file changed",
        }
    )
    return 0


def _cmd_policy(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.workspace)
    except PolicyError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2
    _write_output(
        {
            "allow-licenses": ", ".join(sorted(policy.allowed)),
            "allow-dependencies-licenses": ", ".join(policy.exempt),
            "fail-on-severity": policy.fail_on_severity,
        }
    )
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.workspace)
        sbom = _read_json(args.sbom, "SBOM")
        if not isinstance(sbom, dict):
            raise PolicyError("SBOM is not a JSON object")
        result = evaluate(sbom, policy)
    except PolicyError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for row in result["components"]:
        if row["verdict"] in ("denied", "not_allowed"):
            print(
                f"::error::{row['name']} {row['version']}: licence "
                f"{row['licence'] or '(none)'} is {row['verdict'].replace('_', ' ')}"
            )
        elif row["verdict"] == "unknown":
            print(f"::warning::{row['name']} {row['version']}: licence not determined")
    print(f"Licences: {result['status']} {json.dumps(result['counts'])}")
    if result["status"] == "empty":
        print("::error::the SBOM lists no packages; an empty inventory proves nothing")
        return 1
    return 0 if result["status"] == "pass" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="supply_chain")
    parser.add_argument("--workspace", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("changes")
    sub.add_parser("policy")
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--sbom", type=Path, required=True)
    evaluate_parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    handlers = {
        "changes": _cmd_changes,
        "policy": _cmd_policy,
        "evaluate": _cmd_evaluate,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
