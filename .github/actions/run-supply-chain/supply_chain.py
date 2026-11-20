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


def _load_base(default: Path) -> tuple[set[str], set[str], dict[str, str], str]:
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
    return allowed, denied, dict(aliases), str(severity)


def _load_repo(workspace: Path, denied: set[str]) -> tuple[set[str], list[str]]:
    """(additional allowed licences, exempt purls) from the repository policy."""
    path = workspace / REPO_POLICY
    if path.is_symlink():
        raise PolicyError(f"{REPO_POLICY} must not be a symlink")
    if not path.is_file():
        return set(), []
    repo = _read_json(path, str(REPO_POLICY))
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
            repo.get("additional_allowed_licenses", []), "additional_allowed_licenses"
        )
    )
    conflict = extra & denied
    if conflict:
        raise PolicyError(
            f"{REPO_POLICY} cannot allow a Core-denied licence: {sorted(conflict)}"
        )
    exempt = _strings(
        repo.get("allow_dependencies_licenses", []), "allow_dependencies_licenses"
    )
    bad = [p for p in exempt if not PURL.match(p)]
    if bad:
        raise PolicyError(f"allow_dependencies_licenses entries must be purls: {bad}")
    return extra, exempt


def load_policy(workspace: Path, default: Path = DEFAULT_POLICY) -> Policy:
    allowed, denied, aliases, severity = _load_base(default)
    extra, exempt = _load_repo(workspace, denied)
    return Policy(
        fail_on_severity=severity,
        allowed=frozenset((allowed | extra) - denied),
        denied=frozenset(denied),
        aliases=aliases,
        exempt=tuple(sorted(set(exempt))),
    )


# --- licence expressions --------------------------------------------------

_TOKEN = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[^\s()]+")


def _tokens(expression: str) -> list[str]:
    return _TOKEN.findall(expression)


class _Parser:
    """SPDX expression -> nested ("OR"|"AND", [...]) or a licence id string."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _join(self, operator: str, operand: Any) -> Any:
        parts = [operand()]
        while self._peek() == operator:
            self.pos += 1
            parts.append(operand())
        return (operator, parts) if len(parts) > 1 else parts[0]

    def expression(self) -> Any:
        return self._join("OR", self.term)

    def term(self) -> Any:
        return self._join("AND", self.atom)

    def atom(self) -> Any:
        token = self._peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        if token == "(":
            inner = self.expression()
            if self._peek() != ")":
                raise ValueError("unbalanced parenthesis")
            self.pos += 1
            return inner
        if token in (")", "AND", "OR", "WITH"):
            raise ValueError(f"unexpected {token}")
        if self._peek() == "WITH":
            # An exception narrows the licence it modifies; judge the licence.
            self.pos += 2
        return token


def _parse(tokens: list[str]) -> Any:
    parser = _Parser(tokens)
    tree = parser.expression()
    if parser.pos != len(tokens):
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


def _row(component: dict[str, Any], policy: Policy) -> dict[str, str]:
    purl = str(component.get("purl") or "")
    base_purl = purl.split("@", 1)[0].split("?", 1)[0]
    verdict, rendered = classify_licences(_component_licences(component), policy)
    if verdict != "allowed" and base_purl in policy.exempt:
        verdict = "exempt"
    return {
        "name": str(component.get("name") or "?"),
        "version": str(component.get("version") or "?"),
        "purl": purl,
        "licence": rendered,
        "verdict": verdict,
    }


def _status(counts: dict[str, int]) -> str:
    if not counts:
        return "empty"
    if counts.get("denied") or counts.get("not_allowed"):
        return "fail"
    return "pass"


def evaluate(sbom: dict[str, Any], policy: Policy) -> dict[str, Any]:
    components = sbom.get("components")
    if not isinstance(components, list):
        raise PolicyError("SBOM has no components list")
    rows = [
        _row(component, policy)
        for component in components
        if isinstance(component, dict)
        and component.get("type") in (None, "library", "framework")
    ]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return {
        "schema": "l9.supply-chain-licences/v1",
        "status": _status(counts),
        "counts": dict(sorted(counts.items())),
        "components": sorted(rows, key=lambda r: (r["verdict"], r["name"])),
    }


# --- commands -----------------------------------------------------------------


def decide_changes(workspace: Path, env: dict[str, str]) -> dict[str, str]:
    """``run`` and ``reason`` for the lane on this event."""
    event = env.get("EVENT_NAME", "")
    if event not in ("pull_request", "pull_request_target"):
        return {"run": "true", "reason": f"event {event or 'unknown'}"}
    base, head = env.get("BASE_SHA", ""), env.get("HEAD_SHA", "")
    completed = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not base or not head:
        # Undeterminable change set: run rather than skip.
        print(
            f"::warning::cannot list changed files; running: {completed.stderr.strip()}"
        )
        return {"run": "true", "reason": "change set unreadable"}
    changed = [p for p in completed.stdout.splitlines() if DEPENDENCY_FILES.search(p)]
    if not changed:
        return {"run": "false", "reason": "no dependency file changed"}
    return {"run": "true", "reason": ", ".join(changed[:10])}


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
    if args.command == "changes":
        _write_output(decide_changes(args.workspace, dict(os.environ)))
        return 0
    handlers = {
        "policy": _cmd_policy,
        "evaluate": _cmd_evaluate,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
