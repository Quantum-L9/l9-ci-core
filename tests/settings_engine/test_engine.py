"""Settings engine (tools/l9_settings) against a fake GitHub REST API.

``FakeGitHub`` replaces the API at the shared transport boundary
(``tools/l9_settings/transport.py`` ``Transport``); nothing else is stubbed. The
declaration is written to a temporary ``settings/`` tree with the real schema
shape. Proves: an in-sync repository plans nothing and writes nothing; drift
in every section becomes exactly the expected change; UNKNOWN is never read
or written; an unreadable declared setting fails closed with no write even
under apply; undeclared rulesets and environments are left alone; a missing
declared secret is reported and never written; apply writes in plan order.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.parse
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from l9_settings.__main__ import run  # noqa: E402
from l9_settings.declaration import load_declaration, select  # noqa: E402
from l9_settings.engine import Api, apply_plan, plan_settings  # noqa: E402
from l9_settings.errors import SettingsRefused  # noqa: E402
from l9_settings.transport import Response  # noqa: E402

OWNER = "Quantum-L9"
API = "https://api.github.test"
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {
        "org": {
            "type": "object",
            "required": ["schema", "organization"],
            "properties": {"schema": {"const": "l9.github-settings.org/v1"}},
        },
        "repo": {
            "type": "object",
            "required": [
                "schema",
                "repository",
                "visibility",
                "merge",
                "actions",
                "environments",
                "rulesets",
            ],
            "properties": {"schema": {"const": "l9.github-settings.repo/v1"}},
        },
    },
}
RULESET = {
    "name": "main-protection",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"exclude": [], "include": ["refs/heads/main"]}},
    "rules": [
        {"type": "deletion"},
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": "Test Suite"}],
            },
        },
    ],
    "bypass_actors": [],
}


def declared(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "l9.github-settings.repo/v1",
        "repository": "svc",
        "observed": {"at": "2026-11-20T00:00:00Z", "method": "fixture"},
        "visibility": "private",
        "merge": {"allow_merge_commit": False, "allow_squash_merge": True},
        "actions": {
            "access_level": "organization",
            "default_workflow_permissions": "read",
            "can_approve_pull_request_reviews": False,
        },
        "environments": [
            {
                "name": "deploy",
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branches": ["main"],
                },
                "wait_timer": 0,
                "prevent_self_review": False,
                "reviewers": [],
                "required_secrets": ["DEPLOY_KEY"],
            }
        ],
        "rulesets": [copy.deepcopy(RULESET)],
    }
    document.update(overrides)
    return document


class FakeGitHub:
    """In-memory REST state for one private repository `Quantum-L9/svc`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.denied: set[str] = set()
        base = "/repos/Quantum-L9/svc"
        self.base = base
        self.state: dict[tuple[str, str], tuple[int, Any]] = {
            ("GET", base): (
                200,
                {
                    "visibility": "private",
                    "allow_merge_commit": False,
                    "allow_squash_merge": True,
                },
            ),
            ("GET", f"{base}/actions/permissions/access"): (
                200,
                {"access_level": "organization"},
            ),
            ("GET", f"{base}/actions/permissions/workflow"): (
                200,
                {
                    "default_workflow_permissions": "read",
                    "can_approve_pull_request_reviews": False,
                },
            ),
            ("GET", f"{base}/environments"): (
                200,
                {"environments": [{"name": "deploy"}]},
            ),
            ("GET", f"{base}/environments/deploy"): (
                200,
                {
                    "name": "deploy",
                    "protection_rules": [],
                    "deployment_branch_policy": {
                        "protected_branches": False,
                        "custom_branch_policies": True,
                    },
                },
            ),
            ("GET", f"{base}/environments/deploy/deployment-branch-policies"): (
                200,
                {"branch_policies": [{"id": 7, "name": "main", "type": "branch"}]},
            ),
            ("GET", f"{base}/environments/deploy/secrets"): (
                200,
                {"secrets": [{"name": "DEPLOY_KEY"}]},
            ),
            ("GET", f"{base}/rulesets"): (
                200,
                [{"id": 11, "name": "main-protection", "source_type": "Repository"}],
            ),
            ("GET", f"{base}/rulesets/11"): (
                200,
                {
                    **copy.deepcopy(RULESET),
                    "id": 11,
                    "rules": [
                        {"type": "deletion"},
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "strict_required_status_checks_policy": True,
                                "do_not_enforce_on_create": False,
                                "required_status_checks": [{"context": "Test Suite"}],
                            },
                        },
                    ],
                },
            ),
        }

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        split = urllib.parse.urlsplit(url)
        assert f"{split.scheme}://{split.netloc}" == API, url
        path = split.path
        payload = json.loads(body) if body else None
        self.calls.append((method, path, payload))
        if path in self.denied:
            return Response(
                403, b'{"message": "Resource not accessible by integration"}'
            )
        if method != "GET":
            return Response(200 if method != "POST" else 201, b"{}")
        status, data = self.state.get((method, path), (404, {"message": "Not Found"}))
        return Response(status, json.dumps(data).encode("utf-8"))

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]

    def reads(self) -> set[str]:
        return {c[1] for c in self.calls if c[0] == "GET"}


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.github = FakeGitHub()
        self.api = Api(self.github, API)

    def plan(self, document: dict[str, Any]):
        return plan_settings(self.api, OWNER, {"svc": document})

    def test_in_sync_plans_nothing_and_writes_nothing(self) -> None:
        plan = self.plan(declared())
        self.assertEqual([], plan.changes)
        self.assertEqual([], plan.unreadable)
        self.assertEqual([], plan.unappliable)
        self.assertEqual([], apply_plan(self.api, plan))
        self.assertEqual([], self.github.writes())

    def test_merge_and_visibility_drift(self) -> None:
        plan = self.plan(
            declared(visibility="internal", merge={"allow_merge_commit": True})
        )
        self.assertEqual(
            [
                ("visibility", "PATCH", {"visibility": "internal"}),
                ("merge.allow_merge_commit", "PATCH", {"allow_merge_commit": True}),
            ],
            [(c.setting, c.method, c.body) for c in plan.changes],
        )

    def test_actions_drift(self) -> None:
        document = declared()
        document["actions"] = {
            "access_level": "none",
            "default_workflow_permissions": "write",
            "can_approve_pull_request_reviews": "UNKNOWN",
        }
        plan = self.plan(document)
        self.assertEqual(
            [
                ("actions.access_level", "PUT", {"access_level": "none"}),
                (
                    "actions.workflow_permissions",
                    "PUT",
                    {
                        "default_workflow_permissions": "write",
                        "can_approve_pull_request_reviews": False,
                    },
                ),
            ],
            [(c.setting, c.method, c.body) for c in plan.changes],
        )

    def test_access_level_on_public_repository_is_a_note_and_never_read(self) -> None:
        self.github.state[("GET", self.github.base)] = (
            200,
            {
                "visibility": "public",
                "allow_merge_commit": False,
                "allow_squash_merge": True,
            },
        )
        plan = self.plan(declared(visibility="public"))
        self.assertEqual([], plan.changes)
        self.assertNotIn(
            f"{self.github.base}/actions/permissions/access", self.github.reads()
        )
        self.assertTrue(any("applies once the repository is" in n for n in plan.notes))

    def test_going_private_sets_access_after_visibility(self) -> None:
        self.github.state[("GET", self.github.base)] = (
            200,
            {
                "visibility": "public",
                "allow_merge_commit": False,
                "allow_squash_merge": True,
            },
        )
        plan = self.plan(declared(visibility="private"))
        self.assertEqual(
            ["visibility", "actions.access_level"], [c.setting for c in plan.changes]
        )
        self.assertEqual("n/a", plan.changes[1].live)

    def test_unknown_is_never_read_or_written(self) -> None:
        document = declared(
            visibility="UNKNOWN",
            merge="UNKNOWN",
            actions="UNKNOWN",
            environments="UNKNOWN",
            rulesets="UNKNOWN",
        )
        plan = self.plan(document)
        self.assertEqual([], plan.changes)
        self.assertEqual({self.github.base}, self.github.reads())

    def test_unreadable_fails_closed_without_any_write(self) -> None:
        self.github.denied.add(f"{self.github.base}/actions/permissions/workflow")
        plan = self.plan(declared(merge={"allow_merge_commit": True}))
        self.assertEqual(1, len(plan.changes))
        self.assertTrue(
            any("actions.workflow_permissions" in u for u in plan.unreadable)
        )
        with self.assertRaises(SettingsRefused):
            apply_plan(self.api, plan)
        self.assertEqual([], self.github.writes())

    def test_merge_field_absent_from_live_is_unreadable(self) -> None:
        plan = self.plan(declared(merge={"delete_branch_on_merge": True}))
        self.assertTrue(
            any("merge.delete_branch_on_merge" in u for u in plan.unreadable)
        )

    def test_ruleset_extra_live_parameters_are_not_drift(self) -> None:
        self.assertEqual([], self.plan(declared()).changes)

    def test_ruleset_drift_updates_by_id_and_missing_is_created(self) -> None:
        changed = copy.deepcopy(RULESET)
        changed["enforcement"] = "evaluate"
        changed["rules"][1]["parameters"]["required_status_checks"] = [
            {"context": "Other"}
        ]
        added = copy.deepcopy(RULESET)
        added["name"] = "tags"
        added["target"] = "tag"
        plan = self.plan(declared(rulesets=[changed, added]))
        self.assertEqual(
            [
                (
                    "rulesets[main-protection]",
                    "PUT",
                    "/repos/Quantum-L9/svc/rulesets/11",
                ),
                ("rulesets[tags]", "POST", "/repos/Quantum-L9/svc/rulesets"),
            ],
            [(c.setting, c.method, c.path) for c in plan.changes],
        )
        self.assertEqual({"enforcement", "rules"}, set(plan.changes[0].desired))

    def test_undeclared_ruleset_and_environment_are_left_alone(self) -> None:
        self.github.state[("GET", f"{self.github.base}/rulesets")] = (
            200,
            [
                {"id": 11, "name": "main-protection", "source_type": "Repository"},
                {"id": 12, "name": "legacy", "source_type": "Repository"},
            ],
        )
        self.github.state[("GET", f"{self.github.base}/environments")] = (
            200,
            {"environments": [{"name": "deploy"}, {"name": "old"}]},
        )
        plan = self.plan(declared())
        self.assertEqual([], plan.changes)
        self.assertTrue(
            any("ruleset 'legacy' is not declared" in n for n in plan.notes)
        )
        self.assertTrue(
            any("environment 'old' is not declared" in n for n in plan.notes)
        )

    def test_missing_environment_is_created_then_branches_added(self) -> None:
        del self.github.state[("GET", f"{self.github.base}/environments/deploy")]
        plan = self.plan(declared())
        self.assertEqual(
            [
                ("environments[deploy]", "PUT"),
                ("environments[deploy].custom_branches+main", "POST"),
            ],
            [(c.setting, c.method) for c in plan.changes],
        )
        self.assertEqual(
            {
                "wait_timer": 0,
                "prevent_self_review": False,
                "reviewers": [],
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
            },
            plan.changes[0].body,
        )
        # A new environment cannot hold the declared secret yet.
        self.assertTrue(
            any("lacks secret(s) DEPLOY_KEY" in u for u in plan.unappliable)
        )

    def test_custom_branch_set_is_exact(self) -> None:
        document = declared()
        document["environments"][0]["deployment_branch_policy"] = {
            "protected_branches": False,
            "custom_branches": ["release"],
        }
        plan = self.plan(document)
        self.assertEqual(
            [
                ("environments[deploy].custom_branches+release", "POST", None),
                ("environments[deploy].custom_branches-main", "DELETE", None),
            ],
            [
                (c.setting, c.method, c.body if c.method == "DELETE" else None)
                for c in plan.changes
            ],
        )

    def test_reviewers_resolve_names_to_ids(self) -> None:
        self.github.state[("GET", "/users/octo")] = (200, {"id": 42, "login": "octo"})
        document = declared()
        document["environments"][0]["reviewers"] = [{"type": "User", "name": "octo"}]
        plan = self.plan(document)
        self.assertEqual(1, len(plan.changes))
        self.assertEqual(
            [{"type": "User", "id": 42}], plan.changes[0].body["reviewers"]
        )

    def test_unresolvable_reviewer_fails_closed(self) -> None:
        document = declared()
        document["environments"][0]["reviewers"] = [{"type": "Team", "name": "ghosts"}]
        plan = self.plan(document)
        self.assertEqual([], plan.changes)
        self.assertTrue(any("reviewer Team:ghosts" in u for u in plan.unreadable))

    def test_invalid_branch_policy_is_refused(self) -> None:
        document = declared()
        document["environments"][0]["deployment_branch_policy"] = {
            "protected_branches": False
        }
        with self.assertRaises(SettingsRefused):
            self.plan(document)

    def test_missing_secret_is_reported_never_written(self) -> None:
        self.github.state[
            ("GET", f"{self.github.base}/environments/deploy/secrets")
        ] = (200, {"secrets": []})
        plan = self.plan(declared())
        self.assertEqual([], plan.changes)
        self.assertTrue(
            any("lacks secret(s) DEPLOY_KEY" in u for u in plan.unappliable)
        )
        self.assertFalse(any("secrets" in c[1] for c in self.github.writes()))

    def test_unreadable_secrets_fail_closed(self) -> None:
        self.github.denied.add(f"{self.github.base}/environments/deploy/secrets")
        plan = self.plan(declared())
        self.assertTrue(any("required_secrets" in u for u in plan.unreadable))

    def test_apply_writes_in_plan_order_and_stops_a_repository_on_failure(self) -> None:
        plan = self.plan(
            declared(visibility="internal", merge={"allow_merge_commit": True})
        )
        self.github.denied.add(self.github.base)
        failures = apply_plan(self.api, plan)
        self.assertEqual(2, len(failures))
        self.assertIn("HTTP 403", failures[0])
        self.assertIn("skipped after an earlier failure", failures[1])
        self.assertEqual(
            [("PATCH", self.github.base, {"visibility": "internal"})],
            self.github.writes(),
        )


class DeclarationAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = pathlib.Path(self._tmp.name) / "settings"
        (self.settings / "repos").mkdir(parents=True)
        (self.settings / "settings.schema.json").write_text(
            json.dumps(SCHEMA), encoding="utf-8"
        )
        (self.settings / "org.yml").write_text(
            yaml.safe_dump(
                {"schema": "l9.github-settings.org/v1", "organization": OWNER}
            ),
            encoding="utf-8",
        )
        self.write("svc", declared())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, document: dict[str, Any]) -> None:
        (self.settings / "repos" / f"{name}.yml").write_text(
            yaml.safe_dump(document), encoding="utf-8"
        )

    def cli(self, *argv: str, github: FakeGitHub | None = None) -> tuple[int, str]:
        out = io.StringIO()
        api = Api(github or FakeGitHub(), API)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = run(
                ["--settings", str(self.settings), "--owner", OWNER, *argv], api=api
            )
        return code, out.getvalue()

    def test_dotfile_declaration_is_loaded(self) -> None:
        self.write(".github", declared(repository=".github"))
        self.assertEqual(
            {".github", "svc"}, set(load_declaration(self.settings, OWNER).repositories)
        )

    def test_file_name_must_match_repository(self) -> None:
        self.write("other", declared())
        with self.assertRaises(SettingsRefused):
            load_declaration(self.settings, OWNER)

    def test_schema_violation_refused(self) -> None:
        self.write("svc", {"schema": "l9.github-settings.repo/v1", "repository": "svc"})
        with self.assertRaises(SettingsRefused):
            load_declaration(self.settings, OWNER)

    def test_one_repository_declared_twice_is_refused(self) -> None:
        (self.settings / "repos" / "svc.yaml").write_text(
            yaml.safe_dump(declared(merge={"allow_merge_commit": True})),
            encoding="utf-8",
        )
        with self.assertRaises(SettingsRefused):
            load_declaration(self.settings, OWNER)

    def test_wrong_owner_refused(self) -> None:
        with self.assertRaises(SettingsRefused):
            load_declaration(self.settings, "Someone-Else")

    def test_filter_is_strict(self) -> None:
        declaration = load_declaration(self.settings, OWNER)
        self.assertEqual({"svc"}, set(select(declaration, "Quantum-L9/svc")))
        with self.assertRaises(SettingsRefused):
            select(declaration, "undeclared")

    def test_repositories_stage(self) -> None:
        self.write(".github", declared(repository=".github"))
        code, out = self.cli("repositories")
        self.assertEqual((0, ".github,svc\n"), (code, out))

    def test_plan_exit_codes(self) -> None:
        self.assertEqual(0, self.cli("plan")[0])
        github = FakeGitHub()
        github.denied.add(f"{github.base}/rulesets")
        self.assertEqual(2, self.cli("plan", github=github)[0])
        github = FakeGitHub()
        github.state[("GET", f"{github.base}/environments/deploy/secrets")] = (
            200,
            {"secrets": []},
        )
        self.assertEqual(1, self.cli("plan", github=github)[0])

    def test_plan_never_writes_and_apply_does(self) -> None:
        self.write("svc", declared(merge={"allow_merge_commit": True}))
        github = FakeGitHub()
        code, out = self.cli("plan", github=github)
        self.assertEqual(0, code)
        self.assertIn("~ Quantum-L9/svc merge.allow_merge_commit: false -> true", out)
        self.assertEqual([], github.writes())
        github = FakeGitHub()
        self.assertEqual(0, self.cli("apply", github=github)[0])
        self.assertEqual(
            [("PATCH", github.base, {"allow_merge_commit": True})], github.writes()
        )

    def test_apply_refuses_when_unreadable(self) -> None:
        self.write("svc", declared(merge={"allow_merge_commit": True}))
        github = FakeGitHub()
        github.denied.add(f"{github.base}/environments")
        self.assertEqual(2, self.cli("apply", github=github)[0])
        self.assertEqual([], github.writes())

    def test_plan_json_out(self) -> None:
        out = pathlib.Path(self._tmp.name) / "plan.json"
        self.assertEqual(0, self.cli("plan", "--out", str(out))[0])
        self.assertEqual(
            {"changes": [], "notes": [], "unreadable": [], "unappliable": []},
            json.loads(out.read_text()),
        )


if __name__ == "__main__":
    unittest.main()
