"""Live control-plane attestation (``tools/verify_control_plane.py``).

The verifier is read-only and compares live GitHub state against
``.l9/release-plane.yaml``. These tests drive its comparison logic against
recorded GitHub response shapes through an injected reader, so the normal
suite never needs live organization access and never issues a request.

The rule the assertions defend is that ``UNKNOWN`` is not ``PASS``: absent
credentials, a 403, an unreachable API, and an unrecognised body must all keep
the process exit code non-zero.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "tools" / "verify_control_plane.py"

REPOSITORY = "Quantum-L9/l9-ci-core"
BRANCH = "main"
WORKFLOW = ".github/workflows/org-ci.yml"
REPOSITORY_ID = 1285564308
RULESET_ID = 21895545


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_control_plane", VERIFIER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_verifier()


class FakeReader:
    """Serves recorded GitHub bodies; anything unrecorded is a 404."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def get(self, path: str):
        self.requested.append(path)
        recorded = self.responses.get(path)
        if recorded is None:
            return MODULE.ApiResult(404, error="HTTP 404: Not Found")
        if isinstance(recorded, MODULE.ApiResult):
            return recorded
        return MODULE.ApiResult(200, recorded)


def workflows_rule(
    *,
    repository_id: int = REPOSITORY_ID,
    path: str = WORKFLOW,
    ref: str = f"refs/heads/{BRANCH}",
    source_type: str = "Organization",
    source: str = "Quantum-L9",
    ruleset_id: int = RULESET_ID,
) -> dict:
    return {
        "type": "workflows",
        "ruleset_id": ruleset_id,
        "ruleset_source_type": source_type,
        "ruleset_source": source,
        "parameters": {
            "workflows": [{"repository_id": repository_id, "path": path, "ref": ref}]
        },
    }


def status_checks_rule(contexts: int = 1) -> dict:
    return {
        "type": "required_status_checks",
        "ruleset_id": RULESET_ID,
        "ruleset_source_type": "Organization",
        "ruleset_source": "Quantum-L9",
        "parameters": {
            "required_status_checks": [
                {"context": f"check-{index}"} for index in range(contexts)
            ]
        },
    }


def pull_request_rule(*, code_owner: bool = True) -> dict:
    return {
        "type": "pull_request",
        "ruleset_id": 99,
        "ruleset_source_type": "Repository",
        "ruleset_source": REPOSITORY,
        "parameters": {
            "require_code_owner_review": code_owner,
            "required_approving_review_count": 1,
        },
    }


def responses(
    *,
    rules: list[dict] | None = None,
    enforcement: str = "active",
    immutable: object = None,
) -> dict[str, object]:
    branch_rules = (
        rules
        if rules is not None
        else [workflows_rule(), status_checks_rule(), pull_request_rule()]
    )
    recorded: dict[str, object] = {
        f"repos/{REPOSITORY}": {"id": REPOSITORY_ID, "full_name": REPOSITORY},
        f"repos/{REPOSITORY}/rules/branches/{BRANCH}": branch_rules,
        f"repos/{REPOSITORY}/rulesets/{RULESET_ID}": {
            "id": RULESET_ID,
            "enforcement": enforcement,
        },
        f"repos/{REPOSITORY}/immutable-releases": (
            {"enabled": True, "enforced_by_owner": True}
            if immutable is None
            else immutable
        ),
    }
    return recorded


class ExpectedContractTests(unittest.TestCase):
    """Expectations are read from the contract, never hard-coded here."""

    def test_expectations_come_from_the_real_release_plane(self) -> None:
        expected = MODULE.load_expected(ROOT)
        self.assertEqual(REPOSITORY, expected.repository)
        self.assertEqual(BRANCH, expected.branch)
        self.assertEqual(WORKFLOW, expected.workflow)
        self.assertEqual("Quantum-L9", expected.organization)
        self.assertTrue(expected.immutable_releases)
        self.assertIn("pull_request", expected.required_rule_types)
        self.assertIn("required_status_checks", expected.required_rule_types)
        self.assertGreaterEqual(expected.minimum_bound_contexts, 1)

    def test_release_plane_and_org_runtime_contract_agree(self) -> None:
        import yaml

        expected = MODULE.load_expected(ROOT)
        contract = yaml.safe_load(
            (ROOT / MODULE.ORG_RUNTIME_CONTRACT).read_text(encoding="utf-8")
        )
        binding = contract["entrypoint"]["ruleset_binding"]
        self.assertEqual(expected.repository, binding["repository"])
        self.assertEqual(expected.branch, binding["branch"])
        self.assertEqual(expected.workflow, binding["workflow"])

    def test_disagreeing_binding_contracts_fail_closed(self) -> None:
        """Two contracts, one expectation — a split is ambiguity, not a PASS."""
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".l9").mkdir()
        shutil.copy(ROOT / MODULE.CONTRACT, tmp / MODULE.CONTRACT)
        text = (ROOT / MODULE.ORG_RUNTIME_CONTRACT).read_text(encoding="utf-8")
        (tmp / MODULE.ORG_RUNTIME_CONTRACT).write_text(
            text.replace(f"    branch: {BRANCH}\n", "    branch: release\n", 1),
            encoding="utf-8",
        )
        with self.assertRaises(MODULE.ContractError) as caught:
            MODULE.load_expected(tmp)
        self.assertIn("disagree", str(caught.exception))

    def test_absent_org_runtime_contract_fails_closed(self) -> None:
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".l9").mkdir()
        shutil.copy(ROOT / MODULE.CONTRACT, tmp / MODULE.CONTRACT)
        with self.assertRaises(MODULE.ContractError):
            MODULE.load_expected(tmp)


class ContractDeclaredPathTests(unittest.TestCase):
    """Every path the release plane names must exist.

    A contract that points at a verifier, a workflow, or a test module which is
    not in the tree documents an assurance that does not run. These assertions
    are what stop the attestation block from decaying into a claim.
    """

    def setUp(self) -> None:
        import yaml

        self.plane = yaml.safe_load(
            (ROOT / MODULE.CONTRACT).read_text(encoding="utf-8")
        )

    def test_attestation_declares_this_verifier_and_its_workflow(self) -> None:
        attestation = self.plane["attestation"]["live_control_plane"]
        self.assertEqual("tools/verify_control_plane.py", attestation["verifier"])
        self.assertEqual(
            ".github/workflows/control-plane-attestation.yml", attestation["workflow"]
        )
        for key in ("verifier", "workflow"):
            self.assertTrue((ROOT / attestation[key]).is_file(), attestation[key])

    def test_attestation_declares_read_only_and_unknown_is_not_pass(self) -> None:
        attestation = self.plane["attestation"]["live_control_plane"]
        self.assertFalse(attestation["mutating"])
        self.assertFalse(attestation["unknown_is_pass"])
        self.assertEqual(
            [
                "organization_required_workflow_binding",
                "core_main_protection",
                "immutable_releases",
            ],
            attestation["checks"],
        )

    def test_every_declared_contract_test_exists(self) -> None:
        for relative in self.plane["validation"]["contract_tests"]:
            with self.subTest(test=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)

    def test_main_protection_points_at_real_operator_documentation(self) -> None:
        protection = self.plane["core_main_protection"]
        documented_in = ROOT / protection["documented_in"]
        self.assertTrue(documented_in.is_file(), protection["documented_in"])
        text = documented_in.read_text(encoding="utf-8")
        self.assertIn("## Core `main` protection", text)
        self.assertFalse(
            protection["required_status_checks"]["contexts_declared_here"],
            "L9-ORG-009 keeps required check contexts evidence-backed, so the "
            "contract must not name them",
        )


class ContractLoadingFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".l9").mkdir()

    def write(self, body: str) -> None:
        (self.tmp / MODULE.CONTRACT).write_text(body, encoding="utf-8")

    def test_absent_contract_is_an_error(self) -> None:
        with self.assertRaises(MODULE.ContractError):
            MODULE.load_expected(self.tmp)
        self.assertEqual(4, MODULE.main(["--root", str(self.tmp)]))

    def test_contract_without_a_production_source_is_an_error(self) -> None:
        self.write("schema: l9.release-plane/v1\n")
        with self.assertRaises(MODULE.ContractError):
            MODULE.load_expected(self.tmp)

    def test_contract_without_main_protection_is_an_error(self) -> None:
        self.write(
            "production:\n"
            f"  source: {{repository: {REPOSITORY}, branch: main, "
            f"workflow: {WORKFLOW}}}\n"
            "core_release: {immutable: true}\n"
        )
        with self.assertRaises(MODULE.ContractError):
            MODULE.load_expected(self.tmp)


class AttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expected = MODULE.load_expected(ROOT)

    def run_checks(self, **kwargs) -> dict[str, object]:
        reader = FakeReader(responses(**kwargs))
        results = MODULE.verify(reader, self.expected)
        self.by_name = {result.name: result for result in results}
        self.results = results
        return self.by_name

    def status(self, name: str) -> str:
        return self.by_name[name].status

    # -- all expected conditions present ----------------------------------

    def test_fully_conforming_control_plane_passes(self) -> None:
        self.run_checks()
        for name in (
            MODULE.BINDING_CHECK,
            MODULE.PROTECTION_CHECK,
            MODULE.IMMUTABLE_CHECK,
        ):
            self.assertEqual(MODULE.PASS, self.status(name), name)
        self.assertEqual(0, MODULE.exit_code(self.results))

    def test_no_request_is_a_mutation(self) -> None:
        reader = FakeReader(responses())
        MODULE.verify(reader, self.expected)
        self.assertTrue(reader.requested)
        self.assertFalse(hasattr(MODULE.GitHubReader, "post"))
        self.assertFalse(hasattr(MODULE.GitHubReader, "put"))
        self.assertFalse(hasattr(MODULE.GitHubReader, "delete"))

    # -- binding mismatches ------------------------------------------------

    def test_mismatched_repository_fails(self) -> None:
        self.run_checks(
            rules=[
                workflows_rule(repository_id=REPOSITORY_ID + 1),
                status_checks_rule(),
                pull_request_rule(),
            ]
        )
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))
        self.assertIn("another", self.by_name[MODULE.BINDING_CHECK].reason)

    def test_mismatched_branch_fails(self) -> None:
        self.run_checks(
            rules=[
                workflows_rule(ref="refs/heads/release"),
                status_checks_rule(),
                pull_request_rule(),
            ]
        )
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))

    def test_mismatched_workflow_path_fails(self) -> None:
        self.run_checks(
            rules=[
                workflows_rule(path=".github/workflows/other.yml"),
                status_checks_rule(),
                pull_request_rule(),
            ]
        )
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))

    def test_same_filename_from_a_repository_ruleset_is_not_the_binding(self) -> None:
        """A repository-owned ruleset may not stand in for the organization's."""
        self.run_checks(
            rules=[
                workflows_rule(source_type="Repository", source=REPOSITORY),
                status_checks_rule(),
                pull_request_rule(),
            ]
        )
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))

    def test_binding_from_another_organization_fails(self) -> None:
        self.run_checks(
            rules=[
                workflows_rule(source="Other-Org"),
                status_checks_rule(),
                pull_request_rule(),
            ]
        )
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))

    def test_absent_required_workflow_rule_fails(self) -> None:
        self.run_checks(rules=[status_checks_rule(), pull_request_rule()])
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))

    def test_inactive_ruleset_fails(self) -> None:
        self.run_checks(enforcement="evaluate")
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))
        self.assertIn("evaluate", self.by_name[MODULE.BINDING_CHECK].actual)

    def test_disabled_ruleset_fails(self) -> None:
        self.run_checks(enforcement="disabled")
        self.assertEqual(MODULE.FAIL, self.status(MODULE.BINDING_CHECK))

    def test_unreadable_ruleset_detail_still_passes_on_endpoint_evidence(self) -> None:
        """The branch-rules endpoint returns active rulesets only.

        A credential that can read effective branch rules but not the ruleset
        object has still observed enforcement; the evidence trail says which
        source was used rather than claiming the stronger one.
        """
        recorded = responses()
        recorded[f"repos/{REPOSITORY}/rulesets/{RULESET_ID}"] = MODULE.ApiResult(
            403, error="HTTP 403: Resource not accessible by integration"
        )
        results = MODULE.verify(FakeReader(recorded), self.expected)
        binding = next(r for r in results if r.name == MODULE.BINDING_CHECK)
        self.assertEqual(MODULE.PASS, binding.status)
        self.assertTrue(
            any("active rulesets only" in item for item in binding.evidence)
        )

    # -- main protection ---------------------------------------------------

    def test_absent_main_protection_fails(self) -> None:
        self.run_checks(rules=[workflows_rule(), status_checks_rule()])
        self.assertEqual(MODULE.FAIL, self.status(MODULE.PROTECTION_CHECK))
        self.assertIn("pull_request", self.by_name[MODULE.PROTECTION_CHECK].reason)

    def test_pull_request_without_code_owner_review_fails(self) -> None:
        self.run_checks(
            rules=[
                workflows_rule(),
                status_checks_rule(),
                pull_request_rule(code_owner=False),
            ]
        )
        self.assertEqual(MODULE.FAIL, self.status(MODULE.PROTECTION_CHECK))
        self.assertIn("code-owner", self.by_name[MODULE.PROTECTION_CHECK].reason)

    def test_no_bound_status_check_contexts_fails(self) -> None:
        self.run_checks(
            rules=[workflows_rule(), status_checks_rule(0), pull_request_rule()]
        )
        self.assertEqual(MODULE.FAIL, self.status(MODULE.PROTECTION_CHECK))
        self.assertIn("bound", self.by_name[MODULE.PROTECTION_CHECK].reason)

    def test_unreadable_branch_rules_is_unknown_not_pass(self) -> None:
        recorded = responses()
        recorded[f"repos/{REPOSITORY}/rules/branches/{BRANCH}"] = MODULE.ApiResult(
            403, error="HTTP 403: Resource not accessible by integration"
        )
        results = MODULE.verify(FakeReader(recorded), self.expected)
        for result in results[:2]:
            self.assertEqual(MODULE.UNKNOWN, result.status, result.name)
        self.assertNotEqual(0, MODULE.exit_code(results))

    # -- immutable releases ------------------------------------------------

    def test_immutable_releases_disabled_fails(self) -> None:
        self.run_checks(immutable={"enabled": False, "enforced_by_owner": False})
        self.assertEqual(MODULE.FAIL, self.status(MODULE.IMMUTABLE_CHECK))

    def test_immutable_releases_forbidden_is_unknown_not_pass(self) -> None:
        self.run_checks(
            immutable=MODULE.ApiResult(
                403, error="HTTP 403: Resource not accessible by integration"
            )
        )
        self.assertEqual(MODULE.UNKNOWN, self.status(MODULE.IMMUTABLE_CHECK))
        self.assertEqual(3, MODULE.exit_code(self.results))

    def test_immutable_releases_unreachable_is_unknown_not_pass(self) -> None:
        self.run_checks(
            immutable=MODULE.ApiResult(0, error="request failed: connection refused")
        )
        self.assertEqual(MODULE.UNKNOWN, self.status(MODULE.IMMUTABLE_CHECK))

    def test_unrecognised_immutable_body_is_unknown_not_pass(self) -> None:
        self.run_checks(immutable={"unexpected": "shape"})
        self.assertEqual(MODULE.UNKNOWN, self.status(MODULE.IMMUTABLE_CHECK))

    # -- exit-code discipline ---------------------------------------------

    def test_fail_outranks_unknown_in_the_exit_code(self) -> None:
        results = [
            MODULE.CheckResult("a", MODULE.FAIL),
            MODULE.CheckResult("b", MODULE.UNKNOWN),
        ]
        self.assertEqual(2, MODULE.exit_code(results))

    def test_unknown_alone_is_still_non_zero(self) -> None:
        self.assertEqual(3, MODULE.exit_code([MODULE.CheckResult("a", MODULE.UNKNOWN)]))

    def test_all_pass_is_the_only_zero(self) -> None:
        self.assertEqual(0, MODULE.exit_code([MODULE.CheckResult("a", MODULE.PASS)]))


class CredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expected = MODULE.load_expected(ROOT)

    def test_token_is_read_from_the_documented_variables(self) -> None:
        for name in MODULE.TOKEN_VARIABLES:
            self.assertEqual("t", MODULE.resolve_token({name: "t"}))
        self.assertEqual("", MODULE.resolve_token({}))
        self.assertEqual("", MODULE.resolve_token({"GH_TOKEN": "   "}))

    def test_absent_credentials_produce_unknown_for_every_check(self) -> None:
        results = MODULE.unverifiable(self.expected, "no credential")
        self.assertEqual(3, len(results))
        for result in results:
            self.assertEqual(MODULE.UNKNOWN, result.status)
        self.assertEqual(3, MODULE.exit_code(results))

    def test_main_without_credentials_never_exits_zero(self) -> None:
        import unittest.mock

        with unittest.mock.patch.object(MODULE, "resolve_token", return_value=""):
            self.assertNotEqual(0, MODULE.main(["--root", str(ROOT)]))


class RenderingTests(unittest.TestCase):
    def test_pass_renders_a_single_line(self) -> None:
        rendered = MODULE.CheckResult(MODULE.BINDING_CHECK, MODULE.PASS).render()
        self.assertEqual(f"PASS {MODULE.BINDING_CHECK}", rendered)

    def test_failure_renders_expected_and_actual(self) -> None:
        rendered = MODULE.CheckResult(
            MODULE.BINDING_CHECK,
            MODULE.FAIL,
            expected=f"{REPOSITORY} / {BRANCH} / {WORKFLOW}",
            actual="something else",
        ).render()
        self.assertIn(f"FAIL {MODULE.BINDING_CHECK}", rendered)
        self.assertIn(f"expected: {REPOSITORY} / {BRANCH} / {WORKFLOW}", rendered)
        self.assertIn("actual:   something else", rendered)

    def test_json_conclusion_is_never_pass_with_an_unknown(self) -> None:
        expected = MODULE.load_expected(ROOT)
        document = MODULE.as_json(
            [
                MODULE.CheckResult(MODULE.BINDING_CHECK, MODULE.PASS),
                MODULE.CheckResult(MODULE.IMMUTABLE_CHECK, MODULE.UNKNOWN),
            ],
            expected,
        )
        self.assertEqual(MODULE.UNKNOWN, document["conclusion"])
        self.assertFalse(document["mutating"])


if __name__ == "__main__":
    unittest.main()
