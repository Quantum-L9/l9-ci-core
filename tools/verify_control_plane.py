#!/usr/bin/env python3
"""Read-only attestation of the live GitHub control plane.

Repository-local tests prove Core's contracts agree with each other. They
cannot prove that GitHub is configured the way those contracts describe. This
verifier closes that gap by comparing live GitHub state against
``.l9/release-plane.yaml``:

``organization required-workflow binding``
    the active organization ruleset resolves to the contract's source
    repository, source branch, and workflow path;
``Core main protection``
    the production branch carries the protection the contract declares;
``immutable releases``
    GitHub immutable-release protection is enabled for the repository.

It is attestation only. Every request is a ``GET``; nothing here creates or
edits a ruleset, branch, repository setting, release, or tag.

Three outcomes are distinguished and only one of them is success: ``PASS``,
``FAIL``, and ``UNKNOWN``. Absent credentials, insufficient permissions, an
unreachable API, and an unrecognised response are all ``UNKNOWN`` — never
``PASS``. The process exits non-zero unless every check is ``PASS``.

    python3 tools/verify_control_plane.py [--root PATH] [--json]

Credentials come from ``L9_CONTROL_PLANE_TOKEN``, ``GH_TOKEN``, or
``GITHUB_TOKEN`` — the GitHub CLI/Actions conventions this repository already
uses. No new secret architecture is introduced. This is Core
governance/release assurance: no governed downstream repository runs it and
none needs organization-admin credentials.

Exit codes: ``0`` every check PASS, ``2`` at least one FAIL, ``3`` at least
one UNKNOWN and no FAIL, ``4`` the local contract could not be read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONTRACT = Path(".l9") / "release-plane.yaml"
ORG_RUNTIME_CONTRACT = Path(".l9") / "org-runtime-contract.yaml"
API_ROOT = "https://api.github.com"
TOKEN_VARIABLES = ("L9_CONTROL_PLANE_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

BINDING_CHECK = "organization required-workflow binding"
PROTECTION_CHECK = "Core main protection"
IMMUTABLE_CHECK = "immutable releases"

#: Ruleset enforcement states. Only ``active`` enforces; ``evaluate`` reports
#: without blocking and ``disabled`` does nothing, so neither is a PASS.
ENFORCED = "active"


class ContractError(RuntimeError):
    """The local contract could not be read, so nothing can be compared."""


@dataclass(frozen=True)
class Expected:
    """What ``.l9/release-plane.yaml`` says the control plane must look like."""

    repository: str
    branch: str
    workflow: str
    protection_branch: str
    required_rule_types: tuple[str, ...]
    require_code_owner_review: bool
    minimum_bound_contexts: int
    immutable_releases: bool

    @property
    def organization(self) -> str:
        return self.repository.split("/", 1)[0]


@dataclass(frozen=True)
class ApiResult:
    """One GitHub response, or the reason there is not one.

    ``status`` is the HTTP status, or ``0`` when the request never completed.
    A result is never coerced into a body: an unreadable endpoint stays
    unreadable all the way to the report.
    """

    status: int
    body: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and self.error is None

    def describe(self) -> str:
        if self.error:
            return self.error
        return f"HTTP {self.status}"


@dataclass
class CheckResult:
    name: str
    status: str
    expected: str = ""
    actual: str = ""
    reason: str = ""
    evidence: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"{self.status} {self.name}"]
        if self.status != PASS:
            if self.expected:
                lines.append(f"  expected: {self.expected}")
            if self.actual:
                lines.append(f"  actual:   {self.actual}")
            if self.reason:
                lines.append(f"  reason:   {self.reason}")
        return "\n".join(lines)


class GitHubReader:
    """Minimal read-only GitHub REST client.

    Only ``GET`` is implemented, deliberately: the attestation has no mutating
    path to reach for even by mistake.
    """

    def __init__(self, token: str, *, timeout: int = 30) -> None:
        self._token = token
        self._timeout = timeout

    def get(self, path: str) -> ApiResult:
        url = f"{API_ROOT}/{path.lstrip('/')}"
        if not url.startswith(f"{API_ROOT}/"):
            return ApiResult(0, error=f"refusing non-GitHub API URL: {url!r}")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Quantum-L9-l9-ci-core-control-plane-attestation",
            },
        )
        try:
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- https GitHub API prefix enforced above
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                payload = response.read().decode("utf-8")
                return ApiResult(response.status, json.loads(payload))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:200]
            return ApiResult(error.code, error=f"HTTP {error.code}: {detail}")
        except urllib.error.URLError as error:
            return ApiResult(0, error=f"request failed: {error}")
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return ApiResult(0, error=f"unreadable response body: {error}")


def load_expected(root: Path) -> Expected:
    path = root / CONTRACT
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ContractError(f"cannot read {CONTRACT}: {error}") from error
    except yaml.YAMLError as error:
        raise ContractError(f"{CONTRACT} is not valid YAML: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{CONTRACT} is not a mapping")

    source = _mapping(document, "production", "source")
    repository = _text(source, "repository", "production.source.repository")
    branch = _text(source, "branch", "production.source.branch")
    workflow = _text(source, "workflow", "production.source.workflow")
    if repository.count("/") != 1:
        raise ContractError("production.source.repository must be owner/name")

    protection = _mapping(document, "core_main_protection")
    rule_types = protection.get("required_rule_types")
    if not isinstance(rule_types, list) or not rule_types:
        raise ContractError("core_main_protection.required_rule_types is required")
    pull_request = protection.get("pull_request")
    checks = protection.get("required_status_checks")
    if not isinstance(pull_request, dict) or not isinstance(checks, dict):
        raise ContractError(
            "core_main_protection needs pull_request and required_status_checks"
        )
    minimum = checks.get("minimum_bound_contexts")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        raise ContractError(
            "core_main_protection.required_status_checks.minimum_bound_contexts "
            "must be a positive integer"
        )

    release = _mapping(document, "core_release")
    immutable = release.get("immutable")
    if not isinstance(immutable, bool):
        raise ContractError("core_release.immutable must be a boolean")

    return Expected(
        repository=repository,
        branch=branch,
        workflow=workflow,
        protection_branch=str(protection.get("branch", branch)),
        required_rule_types=tuple(str(entry) for entry in rule_types),
        require_code_owner_review=bool(pull_request.get("require_code_owner_review")),
        minimum_bound_contexts=minimum,
        immutable_releases=immutable,
    )


def _mapping(document: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = document
    walked: list[str] = []
    for key in keys:
        walked.append(key)
        if not isinstance(current, dict) or key not in current:
            raise ContractError(f"{CONTRACT} declares no {'.'.join(walked)}")
        current = current[key]
    if not isinstance(current, dict):
        raise ContractError(f"{'.'.join(walked)} is not a mapping")
    return current


def _text(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value.strip()


def resolve_token(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    for name in TOKEN_VARIABLES:
        value = source.get(name, "").strip()
        if value:
            return value
    return ""


def _rules_for(result: ApiResult, rule_type: str) -> list[dict[str, Any]]:
    if not isinstance(result.body, list):
        return []
    return [
        rule
        for rule in result.body
        if isinstance(rule, dict) and rule.get("type") == rule_type
    ]


def _unknown(name: str, expected: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status=UNKNOWN, expected=expected, reason=reason)


def check_required_workflow_binding(
    reader: GitHubReader, expected: Expected
) -> CheckResult:
    """Prove the active organization ruleset binds Core main's org-ci.yml.

    The binding is read from the *effective* rules on the production branch,
    which is stronger evidence than a ruleset definition: it is what GitHub
    actually applies. The required-workflow rule identifies its source
    repository by numeric id, so the id is resolved from the repository itself
    rather than trusted from a name — a workflow with the same filename in
    another repository or on another branch must not be accepted.
    """
    label = (
        f"{expected.repository} / {expected.branch} / {expected.workflow} "
        "bound by an active organization ruleset"
    )
    repository = reader.get(f"repos/{expected.repository}")
    if not repository.ok:
        return _unknown(
            BINDING_CHECK,
            label,
            f"cannot read repos/{expected.repository}: {repository.describe()}",
        )
    repository_id = (repository.body or {}).get("id")
    if not isinstance(repository_id, int):
        return _unknown(
            BINDING_CHECK, label, "repository response carried no numeric id"
        )

    rules = reader.get(f"repos/{expected.repository}/rules/branches/{expected.branch}")
    if not rules.ok:
        return _unknown(
            BINDING_CHECK,
            label,
            f"cannot read branch rules for {expected.branch}: {rules.describe()}",
        )
    if not isinstance(rules.body, list):
        return _unknown(BINDING_CHECK, label, "branch rules response was not a list")

    workflow_rules = _rules_for(rules, "workflows")
    if not workflow_rules:
        return CheckResult(
            name=BINDING_CHECK,
            status=FAIL,
            expected=label,
            actual=f"no active required-workflow rule applies to {expected.branch}",
        )

    observed: list[str] = []
    for rule in workflow_rules:
        source_type = str(rule.get("ruleset_source_type", ""))
        source = str(rule.get("ruleset_source", ""))
        parameters = rule.get("parameters")
        entries = parameters.get("workflows") if isinstance(parameters, dict) else None
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            described = (
                f"repository_id={entry.get('repository_id')} "
                f"ref={entry.get('ref')} path={entry.get('path')} "
                f"(ruleset {source_type}:{source})"
            )
            observed.append(described)
            matches = (
                entry.get("repository_id") == repository_id
                and str(entry.get("path", "")) == expected.workflow
                and str(entry.get("ref", "")) == f"refs/heads/{expected.branch}"
                and source_type == "Organization"
                and source == expected.organization
            )
            if not matches:
                continue
            enforcement = _enforcement(reader, expected, rule)
            if enforcement.status != PASS:
                enforcement.name = BINDING_CHECK
                enforcement.expected = label
                return enforcement
            return CheckResult(
                name=BINDING_CHECK,
                status=PASS,
                expected=label,
                actual=described,
                evidence=enforcement.evidence,
            )

    return CheckResult(
        name=BINDING_CHECK,
        status=FAIL,
        expected=label,
        actual="; ".join(observed) or "no required-workflow entries",
        reason=(
            "a required workflow with a matching filename from another "
            "repository, branch, or a non-organization ruleset is not the "
            "declared binding"
        ),
    )


def _enforcement(
    reader: GitHubReader, expected: Expected, rule: dict[str, Any]
) -> CheckResult:
    """Confirm the ruleset carrying ``rule`` is enforced, not merely evaluated.

    ``GET /repos/{owner}/{repo}/rules/branches/{branch}`` documents that rules
    from rulesets in ``evaluate`` or ``disabled`` enforcement are not returned,
    so appearing there is already evidence of enforcement. Where the ruleset
    itself is readable that evidence is corroborated directly; where it is not,
    the endpoint's own guarantee stands and the evidence trail records which
    of the two was used rather than overclaiming.
    """
    ruleset_id = rule.get("ruleset_id")
    endpoint = f"repos/{expected.repository}/rules/branches/{expected.branch}"
    if not isinstance(ruleset_id, int):
        return CheckResult(
            name="",
            status=PASS,
            evidence=[f"{endpoint} (returns active rulesets only)"],
        )
    detail = reader.get(f"repos/{expected.repository}/rulesets/{ruleset_id}")
    if not detail.ok or not isinstance(detail.body, dict):
        return CheckResult(
            name="",
            status=PASS,
            evidence=[
                f"{endpoint} (returns active rulesets only); "
                f"ruleset {ruleset_id} detail unreadable: {detail.describe()}"
            ],
        )
    enforcement = str(detail.body.get("enforcement", ""))
    if enforcement != ENFORCED:
        return CheckResult(
            name="",
            status=FAIL,
            actual=f"ruleset {ruleset_id} enforcement is {enforcement!r}",
            reason="an evaluate-only or disabled ruleset does not enforce",
        )
    return CheckResult(
        name="",
        status=PASS,
        evidence=[f"ruleset {ruleset_id} enforcement={enforcement}"],
    )


def check_core_main_protection(reader: GitHubReader, expected: Expected) -> CheckResult:
    """Prove the production branch carries the protection the contract declares.

    No protection policy is invented here: the required rule types, the
    code-owner review requirement, and the minimum number of bound status
    checks are all read from ``core_main_protection`` in the contract.
    """
    requirements = [
        f"rules {', '.join(expected.required_rule_types)} on "
        f"{expected.repository}:{expected.protection_branch}"
    ]
    if expected.require_code_owner_review:
        requirements.append("pull_request.require_code_owner_review=true")
    requirements.append(
        f"at least {expected.minimum_bound_contexts} bound required status check(s)"
    )
    label = "; ".join(requirements)

    rules = reader.get(
        f"repos/{expected.repository}/rules/branches/{expected.protection_branch}"
    )
    if not rules.ok:
        return _unknown(
            PROTECTION_CHECK,
            label,
            f"cannot read branch rules for {expected.protection_branch}: "
            f"{rules.describe()}",
        )
    if not isinstance(rules.body, list):
        return _unknown(PROTECTION_CHECK, label, "branch rules response was not a list")

    present = {
        str(rule.get("type"))
        for rule in rules.body
        if isinstance(rule, dict) and rule.get("type")
    }
    missing = [name for name in expected.required_rule_types if name not in present]
    problems = []
    if missing:
        problems.append(f"missing active rule(s): {', '.join(missing)}")

    if expected.require_code_owner_review and "pull_request" in present:
        satisfied = any(
            isinstance(rule.get("parameters"), dict)
            and rule["parameters"].get("require_code_owner_review") is True
            for rule in _rules_for(rules, "pull_request")
        )
        if not satisfied:
            problems.append("pull_request does not require code-owner review")

    if "required_status_checks" in present:
        bound = max(
            (
                len(rule["parameters"].get("required_status_checks") or [])
                for rule in _rules_for(rules, "required_status_checks")
                if isinstance(rule.get("parameters"), dict)
            ),
            default=0,
        )
        if bound < expected.minimum_bound_contexts:
            problems.append(
                f"{bound} required status check(s) bound, "
                f"expected at least {expected.minimum_bound_contexts}"
            )

    actual = f"active rules: {', '.join(sorted(present)) or 'none'}"
    if problems:
        return CheckResult(
            name=PROTECTION_CHECK,
            status=FAIL,
            expected=label,
            actual=actual,
            reason="; ".join(problems),
        )
    return CheckResult(
        name=PROTECTION_CHECK, status=PASS, expected=label, actual=actual
    )


def check_immutable_releases(reader: GitHubReader, expected: Expected) -> CheckResult:
    """Prove GitHub immutable-release protection is enabled for the repository.

    ``GET /repos/{owner}/{repo}/immutable-releases`` answers ``enabled`` and
    ``enforced_by_owner``. A 403 means the credential cannot see the setting,
    which is UNKNOWN — an immutable release plane that cannot be observed is
    not an immutable release plane that is confirmed.
    """
    label = f"immutable releases enabled for {expected.repository}"
    if not expected.immutable_releases:
        return CheckResult(
            name=IMMUTABLE_CHECK,
            status=PASS,
            expected="core_release.immutable is false; nothing to attest",
        )
    result = reader.get(f"repos/{expected.repository}/immutable-releases")
    if not result.ok:
        return _unknown(
            IMMUTABLE_CHECK,
            label,
            "cannot read the immutable-releases setting "
            f"({result.describe()}); the credential likely lacks repository "
            "administration:read",
        )
    if not isinstance(result.body, dict) or not isinstance(
        result.body.get("enabled"), bool
    ):
        return _unknown(
            IMMUTABLE_CHECK, label, "response carried no boolean 'enabled' field"
        )
    if not result.body["enabled"]:
        return CheckResult(
            name=IMMUTABLE_CHECK,
            status=FAIL,
            expected=label,
            actual="immutable releases are disabled",
        )
    return CheckResult(
        name=IMMUTABLE_CHECK,
        status=PASS,
        expected=label,
        actual=f"enabled=true enforced_by_owner={result.body.get('enforced_by_owner')}",
    )


def verify(reader: GitHubReader, expected: Expected) -> list[CheckResult]:
    return [
        check_required_workflow_binding(reader, expected),
        check_core_main_protection(reader, expected),
        check_immutable_releases(reader, expected),
    ]


def unverifiable(expected: Expected, reason: str) -> list[CheckResult]:
    return [
        _unknown(BINDING_CHECK, expected.workflow, reason),
        _unknown(PROTECTION_CHECK, expected.protection_branch, reason),
        _unknown(IMMUTABLE_CHECK, expected.repository, reason),
    ]


def exit_code(results: list[CheckResult]) -> int:
    if any(result.status == FAIL for result in results):
        return 2
    if any(result.status != PASS for result in results):
        return 3
    return 0


def render(results: list[CheckResult]) -> str:
    return "\n".join(result.render() for result in results)


def as_json(results: list[CheckResult], expected: Expected) -> dict[str, Any]:
    return {
        "schema": "l9.control-plane-attestation/v1",
        "mutating": False,
        "expected": {
            "repository": expected.repository,
            "branch": expected.branch,
            "workflow": expected.workflow,
            "required_rule_types": list(expected.required_rule_types),
            "require_code_owner_review": expected.require_code_owner_review,
            "minimum_bound_contexts": expected.minimum_bound_contexts,
            "immutable_releases": expected.immutable_releases,
        },
        "conclusion": (
            PASS
            if all(result.status == PASS for result in results)
            else FAIL
            if any(result.status == FAIL for result in results)
            else UNKNOWN
        ),
        "checks": [
            {
                "name": result.name,
                "status": result.status,
                "expected": result.expected,
                "actual": result.actual,
                "reason": result.reason,
                "evidence": result.evidence,
            }
            for result in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attest the live control plane.")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    arguments = parser.parse_args(argv)
    try:
        expected = load_expected(Path(arguments.root).resolve())
    except ContractError as error:
        print(f"verify-control-plane: {error}", file=sys.stderr)
        return 4

    token = resolve_token()
    if token:
        results = verify(GitHubReader(token), expected)
    else:
        results = unverifiable(
            expected,
            "no GitHub credential in "
            f"{', '.join(TOKEN_VARIABLES)}; unverifiable state is not a pass",
        )

    if arguments.json:
        print(json.dumps(as_json(results, expected), indent=2))
    else:
        print(render(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
