"""Plan and apply GitHub settings against live state.

Planning only reads. A declared setting that cannot be read live is recorded
as unreadable, and the run applies nothing (fail closed). ``UNKNOWN`` in the
declaration is never read, compared or written. Nothing is ever deleted except
a custom deployment-branch name that the declaration removes from an
environment's exact set; environments and rulesets that are not declared are
reported and left alone.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .declaration import UNKNOWN
from .errors import SettingsInfrastructureError, SettingsRefused
from .transport import Transport, UrllibTransport

MERGE_KEYS = (
    "allow_merge_commit",
    "allow_squash_merge",
    "allow_rebase_merge",
    "allow_auto_merge",
    "allow_update_branch",
    "delete_branch_on_merge",
    "web_commit_signoff_required",
    "squash_merge_commit_title",
    "squash_merge_commit_message",
    "merge_commit_title",
    "merge_commit_message",
)
WORKFLOW_KEYS = ("default_workflow_permissions", "can_approve_pull_request_reviews")
RULESET_KEYS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
NOT_READABLE = "n/a"
ACCESS_LEVEL = "actions.access_level"
BRANCH_POLICY = "deployment_branch_policy"


def _known(value: Any) -> bool:
    return value != UNKNOWN


def _q(segment: str) -> str:
    return urllib.parse.quote(segment, safe="")


class Api:
    """Thin JSON client over the stubbable transport."""

    def __init__(self, transport: Transport, base: str | None = None) -> None:
        self._transport = transport
        self._base = (
            base or os.environ.get("GITHUB_API_URL") or "https://api.github.com"
        ).rstrip("/")

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        response = self._transport.request(method, self._base + path, data, headers)
        if not response.body:
            return response.status, None
        try:
            return response.status, json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return response.status, None

    def get_all(self, path: str, key: str | None = None) -> tuple[int, list[Any]]:
        """Every page of a list endpoint (``key`` names the list in an object body)."""
        items: list[Any] = []
        page = 1
        separator = "&" if "?" in path else "?"
        while True:
            status, body = self.call(
                "GET", f"{path}{separator}per_page=100&page={page}"
            )
            if status != 200:
                return status, []
            batch = (body or {}).get(key, []) if key else (body or [])
            items.extend(batch)
            if len(batch) < 100:
                return 200, items
            page += 1


def api_from_environment() -> Api:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SettingsInfrastructureError("GH_TOKEN or GITHUB_TOKEN is required")
    return Api(UrllibTransport(token))


@dataclass(frozen=True)
class Change:
    repository: str
    setting: str
    live: Any
    desired: Any
    method: str
    path: str
    body: Any = None

    def line(self) -> str:
        live = (
            self.live
            if self.live == NOT_READABLE
            else json.dumps(self.live, sort_keys=True)
        )
        return f"~ {self.repository} {self.setting}: {live} -> {json.dumps(self.desired, sort_keys=True)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "setting": self.setting,
            "live": self.live,
            "desired": self.desired,
            "method": self.method,
            "path": self.path,
        }


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    unappliable: list[str] = field(default_factory=list)

    def extend(self, other: Plan) -> None:
        self.changes += other.changes
        self.notes += other.notes
        self.unreadable += other.unreadable
        self.unappliable += other.unappliable

    def as_dict(self) -> dict[str, Any]:
        return {
            "changes": [c.as_dict() for c in self.changes],
            "notes": self.notes,
            "unreadable": self.unreadable,
            "unappliable": self.unappliable,
        }


class _Repo:
    """Planner for one declared repository."""

    def __init__(
        self, api: Api, owner: str, name: str, declared: dict[str, Any]
    ) -> None:
        self.api = api
        self.owner = owner
        self.name = name
        self.declared = declared
        self.full = f"{owner}/{name}"
        self.base = f"/repos/{_q(owner)}/{_q(name)}"
        self.plan = Plan()

    # -- helpers ---------------------------------------------------------
    def unreadable(self, what: str, status: int) -> None:
        self.plan.unreadable.append(
            f"{self.full} {what}: live read failed (HTTP {status})"
        )

    def change(
        self, setting: str, live: Any, desired: Any, method: str, path: str, body: Any
    ) -> None:
        self.plan.changes.append(
            Change(self.full, setting, live, desired, method, path, body)
        )

    # -- sections --------------------------------------------------------
    def run(self) -> Plan:
        status, repo = self.api.call("GET", self.base)
        if status != 200 or not isinstance(repo, dict):
            self.unreadable("repository", status)
            return self.plan
        live_visibility = repo.get("visibility")
        self.visibility(live_visibility)
        self.merge(repo)
        desired_visibility = self.declared["visibility"]
        effective = (
            desired_visibility if _known(desired_visibility) else live_visibility
        )
        self.actions(live_visibility, effective)
        self.environments()
        self.rulesets()
        self.releases()
        return self.plan

    def visibility(self, live: Any) -> None:
        desired = self.declared["visibility"]
        if not _known(desired):
            return
        if live is None:
            self.unreadable("visibility", 200)
        elif live != desired:
            self.change(
                "visibility", live, desired, "PATCH", self.base, {"visibility": desired}
            )

    def merge(self, repo: dict[str, Any]) -> None:
        merge = self.declared["merge"]
        if not isinstance(merge, dict):
            return
        for key in MERGE_KEYS:
            desired = merge.get(key, UNKNOWN)
            if not _known(desired):
                continue
            if key not in repo:
                # GitHub omits these fields when the token cannot administer the repository.
                self.plan.unreadable.append(
                    f"{self.full} merge.{key}: absent from the live repository"
                )
            elif repo[key] != desired:
                self.change(
                    f"merge.{key}",
                    repo[key],
                    desired,
                    "PATCH",
                    self.base,
                    {key: desired},
                )

    def actions(self, live_visibility: Any, effective_visibility: Any) -> None:
        actions = self.declared["actions"]
        if not isinstance(actions, dict):
            return
        access = actions["access_level"]
        path = f"{self.base}/actions/permissions/access"
        if _known(access):
            if effective_visibility == "public":
                self.plan.notes.append(
                    f"{self.full} actions.access_level={access} applies once the repository is "
                    "private or internal; GitHub ignores it while public"
                )
            elif live_visibility == "public":
                # The visibility change above runs first; access is set right after it.
                self.change(
                    ACCESS_LEVEL,
                    NOT_READABLE,
                    access,
                    "PUT",
                    path,
                    {"access_level": access},
                )
            else:
                status, body = self.api.call("GET", path)
                if (
                    status != 200
                    or not isinstance(body, dict)
                    or "access_level" not in body
                ):
                    self.unreadable(ACCESS_LEVEL, status)
                elif body["access_level"] != access:
                    self.change(
                        ACCESS_LEVEL,
                        body["access_level"],
                        access,
                        "PUT",
                        path,
                        {"access_level": access},
                    )
        wanted = {k: actions[k] for k in WORKFLOW_KEYS if _known(actions[k])}
        if not wanted:
            return
        path = f"{self.base}/actions/permissions/workflow"
        status, body = self.api.call("GET", path)
        if (
            status != 200
            or not isinstance(body, dict)
            or not all(k in body for k in WORKFLOW_KEYS)
        ):
            self.unreadable("actions.workflow_permissions", status)
            return
        live = {k: body[k] for k in WORKFLOW_KEYS}
        desired = {**live, **wanted}
        if desired != live:
            self.change(
                "actions.workflow_permissions", live, desired, "PUT", path, desired
            )

    # -- environments ------------------------------------------------------
    def _reviewer_id(self, reviewer: dict[str, str]) -> int | None:
        if reviewer["type"] == "User":
            path = f"/users/{_q(reviewer['name'])}"
        else:
            path = f"/orgs/{_q(self.owner)}/teams/{_q(reviewer['name'])}"
        status, body = self.api.call("GET", path)
        if (
            status != 200
            or not isinstance(body, dict)
            or not isinstance(body.get("id"), int)
        ):
            self.unreadable(f"reviewer {reviewer['type']}:{reviewer['name']}", status)
            return None
        return int(body["id"])

    def _live_environment(
        self, name: str, data: dict[str, Any], env_path: str
    ) -> dict[str, Any] | None:
        rules = data.get("protection_rules") or []
        wait = next(
            (r.get("wait_timer", 0) for r in rules if r.get("type") == "wait_timer"), 0
        )
        required = next(
            (r for r in rules if r.get("type") == "required_reviewers"), None
        )
        reviewers: list[dict[str, Any]] = []
        for entry in (required or {}).get("reviewers", []):
            reviewer = entry.get("reviewer") or {}
            label = (
                reviewer.get("login")
                if entry.get("type") == "User"
                else reviewer.get("slug")
            )
            reviewers.append(
                {"type": entry.get("type"), "name": label, "id": reviewer.get("id")}
            )
        policy = data.get("deployment_branch_policy")
        branch: Any = "none"
        custom: dict[str, int] = {}
        if isinstance(policy, dict):
            branch = {"protected_branches": bool(policy.get("protected_branches"))}
            if policy.get("custom_branch_policies"):
                status, items = self.api.get_all(
                    f"{env_path}/deployment-branch-policies", "branch_policies"
                )
                if status != 200:
                    self.unreadable(
                        f"environments[{name}].deployment_branch_policy", status
                    )
                    return None
                custom = {
                    i["name"]: i["id"]
                    for i in items
                    if i.get("type", "branch") == "branch"
                }
                branch["custom_branches"] = sorted(custom)
        return {
            "wait_timer": wait,
            "prevent_self_review": bool(
                (required or {}).get("prevent_self_review", False)
            ),
            "reviewers": reviewers,
            "deployment_branch_policy": branch,
            "custom_ids": custom,
        }

    @staticmethod
    def _branch_policy(declared: Any) -> Any:
        if declared == "none":
            return "none"
        normal: dict[str, Any] = {"protected_branches": declared["protected_branches"]}
        if "custom_branches" in declared:
            normal["custom_branches"] = sorted(declared["custom_branches"])
        return normal

    def environments(self) -> None:
        environments = self.declared["environments"]
        if not isinstance(environments, list):
            return
        status, listed = self.api.get_all(f"{self.base}/environments", "environments")
        if status != 200:
            self.unreadable("environments", status)
            return
        declared_names = {e["name"] for e in environments}
        for other in sorted({e.get("name") for e in listed} - declared_names):
            self.plan.notes.append(
                f"{self.full} environment {other!r} is not declared (left alone)"
            )
        for env in environments:
            self.environment(env)

    def environment(self, env: dict[str, Any]) -> None:
        name = env["name"]
        env_path = f"{self.base}/environments/{_q(name)}"
        status, data = self.api.call("GET", env_path)
        if status == 404:
            live = None
        elif status == 200 and isinstance(data, dict):
            live = self._live_environment(name, data, env_path)
            if live is None:
                return
        else:
            self.unreadable(f"environments[{name}]", status)
            return
        self._refuse_invalid_branch_policy(name, env[BRANCH_POLICY])
        wanted = self._wanted_environment(env)
        current = self._current_environment(live)
        differs = {
            k: v for k, v in wanted.items() if live is None or current.get(k) != v
        }
        if self._needs_put(live, differs, current):
            body = self._environment_body(env, live)
            if body is None:
                return
            self.change(
                f"environments[{name}]",
                None if live is None else {k: current[k] for k in differs},
                differs,
                "PUT",
                env_path,
                body,
            )
        self._custom_branches(name, env_path, wanted.get(BRANCH_POLICY), live)
        self._required_secrets(
            name, env_path, env["required_secrets"], live is not None
        )

    def _refuse_invalid_branch_policy(self, name: str, policy: Any) -> None:
        if (
            _known(policy)
            and policy != "none"
            and policy["protected_branches"] == ("custom_branches" in policy)
        ):
            raise SettingsRefused(
                f"{self.full} environments[{name}]: deployment_branch_policy takes "
                "exactly one of protected_branches: true or custom_branches"
            )

    def _wanted_environment(self, env: dict[str, Any]) -> dict[str, Any]:
        wanted: dict[str, Any] = {
            key: env[key]
            for key in ("wait_timer", "prevent_self_review")
            if _known(env[key])
        }
        if _known(env["reviewers"]):
            wanted["reviewers"] = sorted(
                (r["type"], r["name"]) for r in env["reviewers"]
            )
        if _known(env[BRANCH_POLICY]):
            wanted[BRANCH_POLICY] = self._branch_policy(env[BRANCH_POLICY])
        return wanted

    @staticmethod
    def _current_environment(live: dict[str, Any] | None) -> dict[str, Any]:
        if live is None:
            return {}
        return {
            "wait_timer": live["wait_timer"],
            "prevent_self_review": live["prevent_self_review"],
            "reviewers": sorted((r["type"], r["name"]) for r in live["reviewers"]),
            BRANCH_POLICY: live[BRANCH_POLICY],
        }

    def _needs_put(
        self,
        live: dict[str, Any] | None,
        differs: dict[str, Any],
        current: dict[str, Any],
    ) -> bool:
        """A PUT creates the environment or changes what the PUT itself sets.

        A change confined to custom branch names is made by the branch-policy
        endpoints, not by the environment PUT.
        """
        if live is None or any(k != BRANCH_POLICY for k in differs):
            return True
        return BRANCH_POLICY in differs and self._shape(
            differs[BRANCH_POLICY]
        ) != self._shape(current.get(BRANCH_POLICY))

    @staticmethod
    def _shape(policy: Any) -> Any:
        """The part of a branch policy the environment PUT itself sets."""
        if policy is None or policy == "none":
            return "none"
        return (policy["protected_branches"], "custom_branches" in policy)

    def _environment_body(
        self, env: dict[str, Any], live: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """A full PUT body: declared values, live values where the declaration says UNKNOWN."""
        body: dict[str, Any] = {}
        if _known(env["wait_timer"]):
            body["wait_timer"] = env["wait_timer"]
        elif live is not None:
            body["wait_timer"] = live["wait_timer"]
        if _known(env["prevent_self_review"]):
            body["prevent_self_review"] = env["prevent_self_review"]
        elif live is not None:
            body["prevent_self_review"] = live["prevent_self_review"]
        if _known(env["reviewers"]):
            ids = [self._reviewer_id(r) for r in env["reviewers"]]
            if any(i is None for i in ids):
                return None
            body["reviewers"] = [
                {"type": r["type"], "id": i} for r, i in zip(env["reviewers"], ids)
            ]
        elif live is not None:
            body["reviewers"] = [
                {"type": r["type"], "id": r["id"]} for r in live["reviewers"]
            ]
        policy = env[BRANCH_POLICY]
        if not _known(policy):
            policy = live[BRANCH_POLICY] if live is not None else None
        if policy is not None:
            body["deployment_branch_policy"] = (
                None
                if policy == "none"
                else {
                    "protected_branches": policy["protected_branches"],
                    "custom_branch_policies": "custom_branches" in policy,
                }
            )
        return body

    def _custom_branches(
        self, name: str, env_path: str, desired: Any, live: dict[str, Any] | None
    ) -> None:
        if not isinstance(desired, dict) or "custom_branches" not in desired:
            return
        have = live["custom_ids"] if live is not None else {}
        path = f"{env_path}/deployment-branch-policies"
        for branch in desired["custom_branches"]:
            if branch not in have:
                self.change(
                    f"environments[{name}].custom_branches+{branch}",
                    None,
                    branch,
                    "POST",
                    path,
                    {"name": branch, "type": "branch"},
                )
        for branch, policy_id in sorted(have.items()):
            if branch not in desired["custom_branches"]:
                self.change(
                    f"environments[{name}].custom_branches-{branch}",
                    branch,
                    None,
                    "DELETE",
                    f"{path}/{policy_id}",
                    None,
                )

    def _required_secrets(
        self, name: str, env_path: str, required: Any, exists: bool
    ) -> None:
        if not isinstance(required, list) or not required:
            return
        present: set[str] = set()
        if exists:
            status, items = self.api.get_all(f"{env_path}/secrets", "secrets")
            if status != 200:
                self.unreadable(f"environments[{name}].required_secrets", status)
                return
            present = {i.get("name") for i in items}
        missing = [s for s in required if s not in present]
        if missing:
            self.plan.unappliable.append(
                f"{self.full} environments[{name}] lacks secret(s) {', '.join(missing)} "
                "(values come from Infisical; settings never writes a secret)"
            )

    # -- rulesets ----------------------------------------------------------
    @staticmethod
    def _rules_match(
        declared: list[dict[str, Any]], live: list[dict[str, Any]]
    ) -> bool:
        if sorted(r["type"] for r in declared) != sorted(
            str(r.get("type")) for r in live
        ):
            return False
        remaining = list(live)
        for rule in declared:
            wanted = rule.get("parameters") or {}
            for index, candidate in enumerate(remaining):
                got = candidate.get("parameters") or {}
                if candidate.get("type") == rule["type"] and all(
                    got.get(k) == v for k, v in wanted.items()
                ):
                    del remaining[index]
                    break
            else:
                return False
        return True

    @staticmethod
    def _canonical(items: list[Any]) -> list[str]:
        return sorted(json.dumps(i, sort_keys=True) for i in items)

    def _ruleset_differences(
        self, ruleset: dict[str, Any], detail: dict[str, Any]
    ) -> list[str]:
        differs = [
            key
            for key in ("target", "enforcement", "conditions")
            if detail.get(key) != ruleset[key]
        ]
        if not self._rules_match(ruleset["rules"], detail.get("rules") or []):
            differs.append("rules")
        live_bypass = self._canonical(detail.get("bypass_actors") or [])
        if live_bypass != self._canonical(ruleset["bypass_actors"]):
            differs.append("bypass_actors")
        return differs

    def releases(self) -> None:
        """``releases.immutable``: GitHub immutable releases for the repository.

        Optional: a declaration without ``releases`` plans nothing here. The
        live state is ``GET /repos/{o}/{r}/immutable-releases``; GitHub answers
        404 when immutable releases are not enabled. Enabling is ``PUT`` and
        disabling ``DELETE`` on the same path (Administration: write).
        """
        releases = self.declared.get("releases")
        if not isinstance(releases, dict):
            return
        desired = releases.get("immutable", UNKNOWN)
        if not _known(desired):
            return
        path = f"{self.base}/immutable-releases"
        status, body = self.api.call("GET", path)
        if status == 404:
            live = False
        elif (
            status == 200
            and isinstance(body, dict)
            and isinstance(body.get("enabled"), bool)
        ):
            live = body["enabled"]
        else:
            self.unreadable("releases.immutable", status)
            return
        if live != desired:
            self.change(
                "releases.immutable",
                live,
                desired,
                "PUT" if desired else "DELETE",
                path,
                None,
            )

    def rulesets(self) -> None:
        rulesets = self.declared["rulesets"]
        if not isinstance(rulesets, list):
            return
        status, listed = self.api.get_all(
            f"{self.base}/rulesets?includes_parents=false"
        )
        if status != 200:
            self.unreadable("rulesets", status)
            return
        live = {
            r["name"]: r
            for r in listed
            if r.get("source_type", "Repository") == "Repository"
        }
        declared_names = {r["name"] for r in rulesets}
        for other in sorted(set(live) - declared_names):
            self.plan.notes.append(
                f"{self.full} ruleset {other!r} is not declared (left alone)"
            )
        for ruleset in rulesets:
            body = {k: ruleset[k] for k in RULESET_KEYS}
            setting = f"rulesets[{ruleset['name']}]"
            if ruleset["name"] not in live:
                self.change(
                    setting, None, "create", "POST", f"{self.base}/rulesets", body
                )
                continue
            ruleset_id = live[ruleset["name"]]["id"]
            path = f"{self.base}/rulesets/{ruleset_id}"
            status, detail = self.api.call("GET", path)
            if status != 200 or not isinstance(detail, dict):
                self.unreadable(setting, status)
                continue
            differs = self._ruleset_differences(ruleset, detail)
            if differs:
                self.change(
                    setting,
                    {k: detail.get(k) for k in differs},
                    {k: ruleset[k] for k in differs},
                    "PUT",
                    path,
                    body,
                )


def plan_settings(
    api: Api, owner: str, repositories: dict[str, dict[str, Any]]
) -> Plan:
    plan = Plan()
    for name in sorted(repositories):
        plan.extend(_Repo(api, owner, name, repositories[name]).run())
    return plan


def apply_plan(api: Api, plan: Plan) -> list[str]:
    """Execute every change in order; return the failures (never raises on a write)."""
    if plan.unreadable:
        raise SettingsRefused("a declared setting is unreadable; nothing is applied")
    failures: list[str] = []
    failed_repositories: set[str] = set()
    for change in plan.changes:
        if change.repository in failed_repositories:
            failures.append(
                f"{change.line()}: skipped after an earlier failure in this repository"
            )
            continue
        status, body = api.call(change.method, change.path, change.body)
        if status in (200, 201, 204):
            print(f"applied {change.line()}")
            continue
        message = (body or {}).get("message", "") if isinstance(body, dict) else ""
        failures.append(f"{change.line()}: HTTP {status} {message}".rstrip())
        failed_repositories.add(change.repository)
    return failures
