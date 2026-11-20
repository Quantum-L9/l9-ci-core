"""Core's organization licence policy: render it for dependency review, audit an SBOM.

The policy is Core data, never consumer data (AGENTS.md §3): ``policy.json`` in
this action holds the organization allow-list and the copyleft families that are
never allowed by default, and ``repositories/<Owner>__<Repo>.json`` holds the
per-repository package exemptions, each with a stated reason. A consumer cannot
add an exemption from its own checkout.

``render`` turns the policy into the inputs ``actions/dependency-review-action``
takes (``allow-licenses``, ``allow-dependencies-licenses``), so pull-request
review and the SBOM audit enforce one policy.

``audit`` reads a CycloneDX JSON SBOM and classifies every component:

* ``allowed``   every licence it declares is on the allow-list (an SPDX ``OR``
  expression needs one allowed alternative, ``AND`` needs all);
* ``exempted``  its package URL has a per-repository exemption;
* ``copyleft``  a licence of a denied family (GPL, AGPL, LGPL, SSPL, EUPL) — the
  GPL/AGPL audit; ``LGPL-*`` is never read as ``GPL-*``;
* ``not_allowed`` a declared licence that is neither allowed nor copyleft;
* ``unknown``   the component declares no licence (an SBOM read from lockfiles
  often carries none). Reported with its count; it blocks only with
  ``--unknown fail``.

Exit codes: ``0`` no violation; ``1`` a ``copyleft`` or ``not_allowed`` component
(or ``unknown`` with ``--unknown fail``) — a policy failure, blocking; ``2``
invalid invocation or invalid policy data; ``3`` the SBOM is missing, unreadable,
not CycloneDX JSON, or has no components — an infrastructure failure, fatal in
every mode (AGENTS.md §9).

    python3 license_policy.py render --repository OWNER/REPO [--github-output FILE]
    python3 license_policy.py audit --repository OWNER/REPO --sbom FILE
        [--output RECORD.json] [--unknown report|fail] [--summary FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ACTION_DIR = Path(__file__).resolve().parent
POLICY = ACTION_DIR / "policy.json"
REPOSITORIES = ACTION_DIR / "repositories"
POLICY_SCHEMA = "l9.license-policy/v1"
REPOSITORY_SCHEMA = "l9.license-policy-repository/v1"
RECORD_SCHEMA = "l9.license-audit/v1"
REPOSITORY = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
SPDX_ID = re.compile(r"^[A-Za-z0-9.+-]+$")
VIOLATIONS = ("copyleft", "not_allowed")

# Licence names (not ids) of the copyleft families, as some ecosystems declare them.
NAMED_FAMILIES = (
    (re.compile(r"\bAffero General Public License\b", re.IGNORECASE), "AGPL"),
    (
        re.compile(r"\b(?:Lesser|Library) General Public License\b", re.IGNORECASE),
        "LGPL",
    ),
    (re.compile(r"\bGeneral Public License\b", re.IGNORECASE), "GPL"),
    (re.compile(r"\bServer Side Public License\b", re.IGNORECASE), "SSPL"),
    (re.compile(r"\bEuropean Union Public Licen[cs]e\b", re.IGNORECASE), "EUPL"),
)


class PolicyError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Policy:
    allow: frozenset[str]
    deny_families: tuple[str, ...]
    exemptions: dict[str, dict[str, str]]


def _load_json(path: Path, code: int) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(code, f"{path.name} is unreadable: {exc}") from exc


def normalize_purl(purl: str) -> str:
    """A package URL without version, qualifiers or subpath, lower-cased."""
    base = re.split(r"[@?#]", purl, maxsplit=1)[0]
    return base.lower()


def repository_file(repository: str) -> Path:
    return REPOSITORIES / (repository.replace("/", "__") + ".json")


def load_policy(repository: str) -> Policy:
    if not REPOSITORY.match(repository):
        raise PolicyError(2, f"repository {repository!r} is not OWNER/REPO")
    doc = _load_json(POLICY, 2)
    if not isinstance(doc, dict) or doc.get("schema") != POLICY_SCHEMA:
        raise PolicyError(2, f"policy.json is not {POLICY_SCHEMA}")
    allow, families = doc.get("allow"), doc.get("deny_families")
    if (
        not isinstance(allow, list)
        or not allow
        or not all(isinstance(item, str) and SPDX_ID.match(item) for item in allow)
    ):
        raise PolicyError(2, "policy.json allow must be a non-empty list of SPDX ids")
    if not isinstance(families, list) or not all(
        isinstance(f, str) and f for f in families
    ):
        raise PolicyError(2, "policy.json deny_families must be a list of family names")
    clash = sorted(item for item in allow if family_of(item, tuple(families)))
    if clash:
        raise PolicyError(2, f"policy.json allows a denied-family licence: {clash}")
    exemptions: dict[str, dict[str, str]] = {}
    path = repository_file(repository)
    if path.exists():
        data = _load_json(path, 2)
        if (
            not isinstance(data, dict)
            or data.get("schema") != REPOSITORY_SCHEMA
            or data.get("repository") != repository
            or not isinstance(data.get("exemptions"), list)
        ):
            raise PolicyError(
                2, f"{path.name} is not {REPOSITORY_SCHEMA} for {repository}"
            )
        for entry in data["exemptions"]:
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) and entry[key].strip()
                for key in ("package", "license", "reason")
            ):
                raise PolicyError(
                    2, f"{path.name}: every exemption names package, license and reason"
                )
            if not entry["package"].startswith("pkg:"):
                raise PolicyError(2, f"{path.name}: {entry['package']!r} is not a purl")
            key = normalize_purl(entry["package"])
            if key in exemptions:
                raise PolicyError(
                    2, f"{path.name}: {entry['package']} is exempted twice"
                )
            exemptions[key] = entry
    return Policy(frozenset(allow), tuple(families), exemptions)


def family_of(license_id: str, families: tuple[str, ...]) -> str | None:
    """The denied family an SPDX id belongs to; ``LGPL-*`` is never ``GPL``."""
    for family in sorted(families, key=len, reverse=True):
        if license_id == family or license_id.startswith(f"{family}-"):
            return family
    return None


def named_family(name: str, families: tuple[str, ...]) -> str | None:
    for pattern, family in NAMED_FAMILIES:
        if family in families and pattern.search(name):
            return family
    return None


def alternatives(expression: str) -> list[list[str]]:
    """An SPDX expression as OR-alternatives of AND-terms; ``WITH`` exceptions dropped."""
    flat = re.sub(r"[()]", " ", expression)
    flat = re.sub(r"\s+WITH\s+\S+", " ", flat, flags=re.IGNORECASE)
    return [
        [
            term
            for term in re.split(r"\s+AND\s+", alt.strip(), flags=re.IGNORECASE)
            if term
        ]
        for alt in re.split(r"\s+OR\s+", flat.strip(), flags=re.IGNORECASE)
        if alt.strip()
    ]


def classify_licence_set(
    choices: list[list[str]], names: list[str], policy: Policy
) -> tuple[str, list[str]]:
    """The class of one component from its licence alternatives and free-text names."""
    found = sorted(
        {
            family
            for alt in choices
            for term in alt
            if (family := family_of(term, policy.deny_families))
        }
        | {
            family
            for name in names
            if (family := named_family(name, policy.deny_families))
        }
    )
    if any(all(term in policy.allow for term in alt) for alt in choices if alt):
        return "allowed", []
    if found:
        return "copyleft", found
    if not choices and not names:
        return "unknown", []
    return "not_allowed", []


def component_licences(component: dict[str, Any]) -> tuple[list[list[str]], list[str]]:
    choices: list[list[str]] = []
    names: list[str] = []
    for item in component.get("licenses") or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("expression"), str):
            choices.extend(alternatives(item["expression"]))
        licence = item.get("license")
        if isinstance(licence, dict):
            if isinstance(licence.get("id"), str):
                choices.append([licence["id"]])
            elif isinstance(licence.get("name"), str):
                names.append(licence["name"])
    return choices, names


def load_sbom(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise PolicyError(3, f"SBOM {path} is missing")
    doc = _load_json(path, 3)
    if not isinstance(doc, dict) or doc.get("bomFormat") != "CycloneDX":
        raise PolicyError(3, f"SBOM {path} is not CycloneDX JSON")
    components = doc.get("components")
    if not isinstance(components, list) or not components:
        raise PolicyError(3, f"SBOM {path} lists no components: nothing was catalogued")
    return [c for c in components if isinstance(c, dict)]


def audit(sbom: Path, repository: str, fail_unknown: bool) -> dict[str, Any]:
    policy = load_policy(repository)
    entries: list[dict[str, Any]] = []
    for component in load_sbom(sbom):
        purl = component.get("purl") if isinstance(component.get("purl"), str) else ""
        choices, names = component_licences(component)
        status, families = classify_licence_set(choices, names, policy)
        exemption = policy.exemptions.get(normalize_purl(purl)) if purl else None
        if status != "allowed" and exemption is not None:
            status = "exempted"
        entries.append(
            {
                "name": component.get("name"),
                "version": component.get("version"),
                "purl": purl or None,
                "licenses": [" AND ".join(alt) for alt in choices] + names,
                "status": status,
                "families": families,
                "exemption_reason": exemption["reason"]
                if status == "exempted" and exemption
                else None,
            }
        )
    counts = {
        key: sum(1 for entry in entries if entry["status"] == key)
        for key in ("allowed", "exempted", "copyleft", "not_allowed", "unknown")
    }
    blocking = [entry for entry in entries if entry["status"] in VIOLATIONS]
    if fail_unknown:
        blocking += [entry for entry in entries if entry["status"] == "unknown"]
    return {
        "schema": RECORD_SCHEMA,
        "repository": repository,
        "sbom": str(sbom),
        "unknown_mode": "fail" if fail_unknown else "report",
        "counts": counts,
        "status": "fail" if blocking else "pass",
        "violations": blocking,
        "components": entries,
    }


def render(repository: str) -> dict[str, str]:
    policy = load_policy(repository)
    return {
        "allow-licenses": ", ".join(sorted(policy.allow)),
        "allow-dependencies-licenses": ", ".join(
            sorted(entry["package"] for entry in policy.exemptions.values())
        ),
    }


def summary(record: dict[str, Any]) -> str:
    counts = record["counts"]
    lines = [
        f"### Licence audit — {record['status'].upper()}",
        "",
        "| allowed | exempted | copyleft | not allowed | unknown |",
        "|---|---|---|---|---|",
        f"| {counts['allowed']} | {counts['exempted']} | {counts['copyleft']} "
        f"| {counts['not_allowed']} | {counts['unknown']} |",
        "",
        f"Unknown licences are `{record['unknown_mode']}`.",
    ]
    for entry in record["violations"]:
        lines.append(
            f"- **{entry['status']}** `{entry['purl'] or entry['name']}`: "
            f"{', '.join(entry['licenses']) or 'no licence declared'}"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rend = sub.add_parser("render")
    rend.add_argument("--repository", required=True)
    rend.add_argument("--github-output", type=Path)
    aud = sub.add_parser("audit")
    aud.add_argument("--repository", required=True)
    aud.add_argument("--sbom", type=Path, required=True)
    aud.add_argument("--output", type=Path)
    aud.add_argument("--summary", type=Path)
    aud.add_argument("--unknown", choices=("report", "fail"), default="report")
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            rendered = render(args.repository)
            if args.github_output is not None:
                with args.github_output.open("a", encoding="utf-8") as handle:
                    for key, value in rendered.items():
                        handle.write(f"{key}={value}\n")
            print(json.dumps(rendered, indent=2, sort_keys=True))
            return 0
        record = audit(args.sbom, args.repository, args.unknown == "fail")
    except PolicyError as exc:
        print(f"::error title=Licence policy::{exc}", file=sys.stderr)
        return exc.code
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(summary(record))
    for entry in record["violations"]:
        print(
            f"::error title=Licence policy::{entry['status']}: "
            f"{entry['purl'] or entry['name']} ({', '.join(entry['licenses']) or 'no licence'})",
            file=sys.stderr,
        )
    print(json.dumps(record["counts"], sort_keys=True))
    return 1 if record["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
