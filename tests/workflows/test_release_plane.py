"""Release-plane contract consistency (``l9.release-plane/v1``).

Core ``main`` is the organization CI runtime, bound directly by the GitHub
organization required-workflow ruleset. Releases are immutable audit anchors
and never a propagation mechanism. These tests make documentation drift
between the release contract, the architecture and runtime contracts, the
SDK allowlist, the entrypoint workflow, and the release gate a failing test
rather than an archaeology exercise.
"""

from __future__ import annotations

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
RELEASE_PLANE = ROOT / ".l9" / "release-plane.yaml"
ARCHITECTURE = ROOT / ".l9" / "architecture.yaml"
ORG_RUNTIME = ROOT / ".l9" / "org-runtime-contract.yaml"
SDK_COMPAT = ROOT / ".l9" / "sdk-compatibility.yaml"
REPO_SPEC = ROOT / ".l9" / "repo-spec.yaml"
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
RELEASE_VALIDATION = ROOT / ".github" / "workflows" / "release-validation.yml"
VALIDATE_RELEASE_ACTION = ROOT / ".github" / "actions" / "validate-release"
RELEASE_README = ROOT / "docs" / "release" / "README.md"
RELEASE_SCRIPT = ROOT / "docs" / "release" / "tag-and-release.sh"
AGENTS = ROOT / "AGENTS.md"

CORE_REPOSITORY = "Quantum-L9/l9-ci-core"
CORE_WORKFLOW = ".github/workflows/org-ci.yml"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def workflow_triggers(path: pathlib.Path) -> dict:
    document = load(path)
    # PyYAML resolves the bare `on:` key to the boolean True.
    return document[True] if True in document else document["on"]


class ReleasePlaneContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plane = load(RELEASE_PLANE)

    def test_contract_identity(self) -> None:
        self.assertEqual("l9.release-plane/v1", self.plane["schema"])
        self.assertEqual("authoritative", self.plane["metadata"]["status"])
        self.assertEqual(CORE_REPOSITORY, self.plane["metadata"]["repository"])
        self.assertEqual(
            CORE_REPOSITORY, self.plane["authority"]["execution_repository"]
        )
        self.assertEqual(
            "Quantum-L9/Cursor-Governance",
            self.plane["authority"]["governance_repository"],
        )

    def test_production_source_is_core_main_via_ruleset(self) -> None:
        production = self.plane["production"]
        self.assertEqual(
            "github_organization_required_workflow_ruleset",
            production["mechanism"],
        )
        self.assertEqual(
            {
                "repository": CORE_REPOSITORY,
                "branch": "main",
                "workflow": CORE_WORKFLOW,
            },
            production["source"],
        )

    def test_consumers_select_nothing(self) -> None:
        consumer = self.plane["production"]["consumer"]
        for key in (
            "workflow_copy_allowed",
            "core_revision_selection_allowed",
            "sdk_revision_selection_allowed",
            "moving_release_tag_required",
            "update_pull_requests_required",
        ):
            self.assertFalse(consumer[key], key)

    def test_releases_are_immutable_audit_anchors_without_runtime_authority(
        self,
    ) -> None:
        release = self.plane["core_release"]
        self.assertTrue(release["immutable"])
        self.assertFalse(release["runtime_authority"])
        self.assertEqual("semver", release["versioning"])
        self.assertEqual("vMAJOR.MINOR.PATCH", release["tag_pattern"])
        self.assertFalse(release["moving_major_alias"]["enabled"])
        for purpose in ("audit", "provenance", "rollback_identity", "release_notes"):
            self.assertIn(purpose, release["purpose"])

    def test_ruleset_events_exclude_push_and_fanout_is_not_claimed(self) -> None:
        events = self.plane["events"]
        self.assertEqual(
            ["pull_request", "merge_group"], events["organization_ruleset"]
        )
        self.assertFalse(events["cross_repository_push_fanout"]["provided_by_ruleset"])

    def test_governance_clarification_is_a_proposal_until_recorded(self) -> None:
        clarification = self.plane["governance_clarification"]
        self.assertTrue(clarification["core_main_protection_required"])
        self.assertFalse(clarification["recorded_in_cursor_governance"])
        self.assertIn("L9-ORG-008", clarification["statement"])
        self.assertIn("L9-ORG-008", self.plane["authority"]["governance_invariants"])
        self.assertIn("L9-ORG-007", self.plane["authority"]["governance_invariants"])


class ReleasePlaneAgreesWithSiblingContractsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plane = load(RELEASE_PLANE)

    def test_architecture_declares_the_same_production_channel(self) -> None:
        architecture = load(ARCHITECTURE)
        channel = architecture["production_channel"]
        self.assertEqual(".l9/release-plane.yaml", channel["contract"])
        self.assertEqual(
            self.plane["production"]["source"]["repository"],
            channel["runtime_source"]["repository"],
        )
        self.assertEqual(
            self.plane["production"]["source"]["branch"],
            channel["runtime_source"]["branch"],
        )
        self.assertEqual(
            self.plane["production"]["source"]["workflow"],
            channel["runtime_source"]["workflow"],
        )
        self.assertEqual(
            self.plane["production"]["mechanism"], channel["runtime_source"]["bound_by"]
        )
        self.assertFalse(channel["releases"]["runtime_authority"])
        self.assertFalse(channel["releases"]["moving_major_alias"])
        self.assertFalse(channel["consumer_core_revision_selection"])
        prohibited = set(architecture["prohibited_architecture"])
        self.assertIn(
            "a moving major release tag as the organization CI runtime channel",
            prohibited,
        )
        self.assertIn(
            "consumer-owned Core revision selection for organization CI", prohibited
        )

    def test_org_runtime_contract_binds_the_same_source(self) -> None:
        contract = load(ORG_RUNTIME)
        entrypoint = contract["entrypoint"]
        self.assertEqual(
            self.plane["production"]["mechanism"], entrypoint["enforcement_mechanism"]
        )
        binding = entrypoint["ruleset_binding"]
        self.assertEqual(
            self.plane["production"]["source"]["repository"], binding["repository"]
        )
        self.assertEqual(
            self.plane["production"]["source"]["branch"], binding["branch"]
        )
        self.assertEqual(
            self.plane["production"]["source"]["workflow"], binding["workflow"]
        )
        self.assertEqual(binding["workflow"], entrypoint["workflow"])
        self.assertEqual("none", binding["consumer_uses_reference"])
        self.assertEqual(
            self.plane["events"]["organization_ruleset"], entrypoint["ruleset_events"]
        )
        self.assertNotIn("push", entrypoint["ruleset_events"])
        self.assertFalse(
            entrypoint["cross_repository_push_fanout"]["provided_by_ruleset"]
        )
        required = contract["pinning"]["required_workflow"]
        self.assertFalse(required["consumer_selected"])
        self.assertEqual("main", required["source_branch"])
        self.assertFalse(required["moving_major_alias_used"])
        self.assertIn(
            "tests/workflows/test_release_plane.py",
            contract["validation"]["contract_tests"],
        )

    def test_sdk_selection_matches_the_compatibility_manifest(self) -> None:
        sdk = self.plane["sdk"]
        compat = load(SDK_COMPAT)
        self.assertEqual(".l9/sdk-compatibility.yaml", sdk["selection"]["manifest"])
        self.assertEqual(CORE_REPOSITORY, sdk["selection"]["authority"])
        self.assertEqual("git_commit_sha", sdk["selection"]["reference_type"])
        self.assertTrue(sdk["selection"]["full_length_required"])
        self.assertFalse(sdk["selection"]["floating_refs_allowed"])
        self.assertTrue(sdk["promotion"]["requires_core_compatibility_validation"])
        self.assertTrue(sdk["promotion"]["requires_governed_core_change"])

        self.assertEqual(
            f"https://github.com/{sdk['repository']}.git",
            compat["default"]["repository"],
        )
        self.assertRegex(compat["default"]["revision"], FULL_SHA)
        for entry in compat["supported"]:
            self.assertRegex(entry["revision"], FULL_SHA)
        policy = compat["policy"]
        self.assertFalse(policy["floating_git_references_allowed"])
        self.assertFalse(policy["branches_allowed"])
        self.assertFalse(policy["tags_allowed"])
        self.assertFalse(policy["short_git_revisions_allowed"])
        self.assertEqual(
            load(ARCHITECTURE)["sdk"]["revision"], compat["default"]["revision"]
        )

    def test_entrypoint_declares_every_ruleset_event(self) -> None:
        triggers = workflow_triggers(ORG_CI)
        for event in self.plane["events"]["organization_ruleset"]:
            self.assertIn(event, triggers, event)
        self.assertIn(
            "push",
            triggers,
            "native push stays declared for Core's own repository; it is "
            "not ruleset fanout",
        )

    def test_release_validation_reads_the_version_from_repo_spec(self) -> None:
        validation = self.plane["core_release"]["validation"]
        self.assertEqual(
            ".github/workflows/release-validation.yml", validation["workflow"]
        )
        self.assertEqual(".l9/repo-spec.yaml", validation["expected_version_source"])
        self.assertFalse(validation["expected_version_hardcoded_in_workflow"])

        text = RELEASE_VALIDATION.read_text(encoding="utf-8")
        self.assertNotRegex(
            text,
            r"(?m)^\s*expected-version:",
            "release-validation.yml must not hard-code a release version",
        )
        triggers = workflow_triggers(RELEASE_VALIDATION)
        self.assertEqual(["v*.*.*"], triggers["push"]["tags"])

        action = load(VALIDATE_RELEASE_ACTION / "action.yml")
        self.assertFalse(action["inputs"]["expected-version"]["required"])
        validator = (VALIDATE_RELEASE_ACTION / "validate_release.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(".l9/release-plane.yaml", validator)
        self.assertNotIn("version: 2.0.0", validator)

    def test_repo_spec_declares_an_exact_release_version(self) -> None:
        version = str(load(REPO_SPEC)["metadata"]["version"])
        self.assertRegex(
            version, r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
        )


class ReleaseDocumentationTests(unittest.TestCase):
    def test_release_script_cuts_exact_versions_and_moves_no_alias(self) -> None:
        text = RELEASE_SCRIPT.read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        self.assertNotIn("ALIAS_TAG", code)
        self.assertNotRegex(code, r"git tag -f")
        self.assertNotRegex(code, r"git push (-f|--force)")
        self.assertNotRegex(code, r"refs/tags/v2\b")
        self.assertNotIn('RELEASE_TAG="v2.0.0"', code)
        self.assertIn("repo-spec.yaml", code)
        self.assertIn("release-validation.yml", text)

    def test_release_script_runs_the_validator_before_creating_the_tag(self) -> None:
        """An invalid immutable tag cannot be moved, so validation comes first.

        The preflight must run the same validator the post-tag workflow runs,
        against an export of the exact target commit (never the operator's
        working tree), and every tag-creating or pushing command must come
        after it.
        """
        plane = load(RELEASE_PLANE)["core_release"]["validation"]
        self.assertTrue(plane["preflight_before_tag"])
        self.assertEqual("docs/release/tag-and-release.sh", plane["preflight_script"])

        text = RELEASE_SCRIPT.read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        validator = code.index("validate-release/validate_release.py")
        # A real checkout, not an archive export: the validation suite
        # enumerates tracked files with git and fails outside a repository.
        self.assertIn("git worktree add --detach", code[:validator])
        self.assertNotIn("git archive", code)
        self.assertIn("GITHUB_WORKSPACE=", code[:validator])
        self.assertIn('L9_RELEASE_TAG="${RELEASE_TAG}"', code[:validator])
        for mutation in ("git tag -a", "git push origin", "gh release create"):
            self.assertGreater(code.index(mutation), validator, mutation)
        self.assertRegex(code, r"(?m)^\s*die .*preflight failed")

    def test_release_lifecycle_orders_preflight_before_the_tag(self) -> None:
        lifecycle = load(RELEASE_PLANE)["core_release"]["lifecycle"]
        preflight = next(i for i, s in enumerate(lifecycle) if "preflight" in s)
        tag = next(i for i, s in enumerate(lifecycle) if "immutable" in s)
        attest = next(i for i, s in enumerate(lifecycle) if "post-tag" in s)
        self.assertLess(preflight, tag)
        self.assertLess(tag, attest)

    def test_release_readme_describes_main_as_the_runtime_channel(self) -> None:
        text = RELEASE_README.read_text(encoding="utf-8")
        self.assertIn(".l9/release-plane.yaml", text)
        self.assertIn(CORE_WORKFLOW, text)
        self.assertIn("L9-ORG-008", text)
        self.assertIn("L9-ORG-007", text)
        self.assertNotIn("moving major alias**", text)
        # No consumer Core pin guidance for the organization path.
        self.assertNotRegex(text, r"Consumers may pin")
        self.assertNotRegex(text, r"`@v2`, or a full commit SHA")

    def test_agents_md_points_at_the_release_plane(self) -> None:
        text = AGENTS.read_text(encoding="utf-8")
        self.assertIn(".l9/release-plane.yaml", text)
        self.assertIn("## 12. Release plane", text)


if __name__ == "__main__":
    unittest.main()
