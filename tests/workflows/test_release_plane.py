"""Release-plane contract consistency (``l9.release-plane/v1``).

Core ``main`` is the organization CI runtime, bound directly by the GitHub
organization required-workflow ruleset. Releases are immutable audit anchors
and never a propagation mechanism. These tests make documentation drift
between the release contract, the architecture and runtime contracts, the
SDK allowlist, the entrypoint workflow, and the release gate a failing test
rather than an archaeology exercise.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
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
OPTIONAL_INTEGRATION_GUIDE = (
    ROOT / "docs" / "release" / "consumer-integration-channel.md"
)
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

    def test_optional_v2_integration_is_named_and_non_authoritative(self) -> None:
        channel = self.plane["core_release"]["optional_integration_channel"]
        self.assertTrue(channel["enabled"])
        self.assertEqual("v2", channel["tag"])
        self.assertEqual(
            [
                {
                    "repository": "Quantum-L9/l9-cognitive-runtime",
                    "workflow": ".github/workflows/release-staging.yml",
                    "permitted_surfaces": [
                        ".github/workflows/analyze-semgrep.yml",
                        ".github/actions/container-release",
                    ],
                }
            ],
            channel["consumers"],
        )
        safeguards = set(channel["safeguards"])
        self.assertIn("never_organization_required_workflow", safeguards)
        self.assertIn("never_exact_core_release_alias", safeguards)

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

    def test_release_validation_binds_both_triggers_to_annotated_tag_identity(
        self,
    ) -> None:
        identity = self.plane["core_release"]["validation"]["post_tag_identity"]
        self.assertEqual("exact_refs_tags_name", identity["remote_ref_source"])
        self.assertEqual("annotated", identity["tag_object_required"])
        self.assertEqual("direct_commit", identity["target_type"])
        self.assertEqual("peeled_commit", identity["checkout"])
        self.assertTrue(identity["push_event_sha_must_equal_peeled_commit"])
        self.assertTrue(identity["manual_dispatch_resolves_same_remote_ref"])
        self.assertFalse(identity["mutating"])

        text = RELEASE_VALIDATION.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\s+contents:\s+read\s*$")
        self.assertIn('"refs/tags/${tag}"', text)
        self.assertIn("fetch --no-tags --depth=1", text)
        self.assertIn("git cat-file -t FETCH_HEAD", text)
        self.assertIn('"${direct_type}" == "commit"', text)
        self.assertIn('"${EVENT_SHA}" != "${commit}"', text)
        self.assertIn('git checkout --detach "${commit}"', text)
        self.assertNotRegex(text, r"(?m)^\s+git\s+(?:tag|push)\b")
        self.assertNotRegex(
            text,
            r"(?m)^\s+(?:gh\s+(?:release|api)|curl\s+-X\s+(?:POST|PATCH|PUT|DELETE))\b",
        )

        action = load(VALIDATE_RELEASE_ACTION / "action.yml")
        self.assertTrue(action["inputs"]["tag-object"]["required"])
        self.assertTrue(action["inputs"]["release-commit"]["required"])
        action_text = (VALIDATE_RELEASE_ACTION / "action.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('L9_RELEASE_PREFLIGHT: "false"', action_text)
        self.assertIn(
            "steps.release.outputs.tag-object",
            text,
        )
        self.assertIn("steps.release.outputs.commit", text)

    def test_repo_spec_declares_an_exact_release_version(self) -> None:
        version = str(load(REPO_SPEC)["metadata"]["version"])
        self.assertRegex(
            version, r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
        )


class ReleaseIdentityResolutionWorkflowTests(unittest.TestCase):
    """Execute the workflow's resolver against local Git tag fixtures."""

    ANNOTATED_TAG = "v3.4.5"
    LIGHTWEIGHT_TAG = "v3.4.6"

    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.remote = self.tmp / "remote.git"
        self.source = self.tmp / "source"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.git(self.tmp, "init", "--bare", "--quiet", str(self.remote))
        self.git(self.tmp, "init", "--quiet", str(self.source))
        self.git(self.source, "config", "user.name", "Release Test")
        self.git(self.source, "config", "user.email", "release-test@example.com")
        (self.source / "release.txt").write_text("release\n", encoding="utf-8")
        self.git(self.source, "add", "release.txt")
        self.git(self.source, "commit", "--quiet", "-m", "release")
        self.commit = self.git(self.source, "rev-parse", "HEAD")
        self.git(
            self.source,
            "tag",
            "-a",
            self.ANNOTATED_TAG,
            "-m",
            self.ANNOTATED_TAG,
        )
        self.tag_object = self.git(
            self.source, "rev-parse", f"refs/tags/{self.ANNOTATED_TAG}"
        )
        self.git(self.source, "tag", self.LIGHTWEIGHT_TAG)
        self.git(self.source, "remote", "add", "origin", str(self.remote))
        self.git(self.source, "push", "--quiet", "origin", "--tags")

        token_url = (
            "https://x-access-token:test-token@github.com/Quantum-L9/l9-ci-core.git"
        )
        self.git(
            self.tmp,
            "config",
            "--file",
            str(self.home / ".gitconfig"),
            f"url.file://{self.remote}.insteadOf",
            token_url,
        )
        workflow = load(RELEASE_VALIDATION)
        self.resolver = workflow["jobs"]["validate"]["steps"][0]["run"]

    @staticmethod
    def git(cwd: pathlib.Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def run_resolver(
        self,
        *,
        event_name: str,
        tag: str,
        event_sha: str,
    ) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, dict[str, str]]:
        workspace = self.tmp / f"run-{event_name}-{tag}"
        workspace.mkdir()
        output = workspace / "github-output"
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_ALLOW_PROTOCOL": "file",
                "EVENT_NAME": event_name,
                "EVENT_REF": f"refs/tags/{tag}",
                "EVENT_SHA": event_sha,
                "REF_NAME": tag,
                "DISPATCH_TAG": tag,
                "REPOSITORY": "Quantum-L9/l9-ci-core",
                "TOKEN": "test-token",
                "GITHUB_OUTPUT": str(output),
            }
        )
        result = subprocess.run(
            ["bash", "-c", self.resolver],
            cwd=workspace,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        outputs: dict[str, str] = {}
        if output.is_file():
            outputs = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
        return result, workspace, outputs

    def test_manual_dispatch_checks_out_the_peeled_annotated_commit(self) -> None:
        result, workspace, outputs = self.run_resolver(
            event_name="workflow_dispatch",
            tag=self.ANNOTATED_TAG,
            event_sha="0" * 40,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.ANNOTATED_TAG, outputs["tag"])
        self.assertEqual(self.tag_object, outputs["tag-object"])
        self.assertEqual(self.commit, outputs["commit"])
        self.assertEqual(self.commit, self.git(workspace, "rev-parse", "HEAD"))

    def test_lightweight_remote_tag_is_rejected(self) -> None:
        result, _, outputs = self.run_resolver(
            event_name="workflow_dispatch",
            tag=self.LIGHTWEIGHT_TAG,
            event_sha="0" * 40,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("not an annotated tag object", result.stderr)
        self.assertEqual({}, outputs)

    def test_push_event_accepts_the_matching_peeled_commit(self) -> None:
        result, workspace, outputs = self.run_resolver(
            event_name="push",
            tag=self.ANNOTATED_TAG,
            event_sha=self.commit,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.tag_object, outputs["tag-object"])
        self.assertEqual(self.commit, outputs["commit"])
        self.assertEqual(self.commit, self.git(workspace, "rev-parse", "HEAD"))

    def test_push_event_sha_must_equal_the_remote_tags_peeled_commit(self) -> None:
        result, _, outputs = self.run_resolver(
            event_name="push",
            tag=self.ANNOTATED_TAG,
            event_sha="f" * 40,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("does not match peeled commit", result.stderr)
        self.assertEqual({}, outputs)


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
        self.assertIn('L9_RELEASE_TAG_OBJECT=""', code[:validator])
        self.assertIn('L9_RELEASE_COMMIT=""', code[:validator])
        self.assertIn('L9_RELEASE_PREFLIGHT="true"', code[:validator])
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
        self.assertIn("consumer-integration-channel.md", text)
        guide = OPTIONAL_INTEGRATION_GUIDE.read_text(encoding="utf-8")
        self.assertIn("Quantum-L9/l9-cognitive-runtime", guide)
        self.assertIn("never an organization required workflow", guide)

    def test_agents_md_points_at_the_release_plane(self) -> None:
        text = AGENTS.read_text(encoding="utf-8")
        self.assertIn(".l9/release-plane.yaml", text)
        self.assertIn("## 12. Release plane", text)


if __name__ == "__main__":
    unittest.main()
