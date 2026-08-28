"""Central organization runtime contract consistency."""

from __future__ import annotations

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / ".l9" / "org-runtime-contract.yaml"
ENTRYPOINT_PATH = ROOT / ".github" / "workflows" / "org-ci.yml"
CONSUMER_SCHEMA_PATH = ROOT / ".l9" / "ci-consumer.schema.json"
PROFILES_PATH = (
    ROOT
    / ".github"
    / "actions"
    / "resolve-governance"
    / "defaults"
    / "execution-profiles.yaml"
)


def load_contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


class OrgRuntimeContractTests(unittest.TestCase):
    def test_contract_declares_central_ruleset_entrypoint(self) -> None:
        contract = load_contract()
        self.assertEqual("l9.org-runtime-contract/v1", contract["schema"])
        self.assertEqual(
            ".github/workflows/org-ci.yml", contract["entrypoint"]["workflow"]
        )
        self.assertEqual(
            "github_organization_required_workflow_ruleset",
            contract["entrypoint"]["enforcement_mechanism"],
        )
        self.assertFalse(contract["entrypoint"]["consumer_copy_required"])
        self.assertFalse(contract["entrypoint"]["consumer_core_pin_allowed"])

    def test_entrypoint_supports_ruleset_canary_and_push_events(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        header = text.split("permissions:", 1)[0]
        for trigger in (
            "push:",
            "pull_request:",
            "merge_group:",
            "workflow_dispatch:",
            "workflow_call:",
        ):
            self.assertIn(trigger, header)
        self.assertNotIn("schedule:", header)
        self.assertRegex(header, r"(?m)^\s{2}push:\s*$")

    def test_push_trigger_declares_no_branch_selector(self) -> None:
        """`push` must stay unfiltered — no hardcoded default branch.

        GitHub Actions cannot express "this repository's default branch"
        symbolically in `on.push.branches`, and Quantum-L9 repositories do not
        all use `main`. A literal branch list here would silently exclude every
        repository whose default branch is named something else, which is the
        opposite of a central enforcement surface. Ref and repository selection
        belongs to the organization ruleset; Core only decides how a governed
        evaluation executes.
        """
        document = yaml.safe_load(ENTRYPOINT_PATH.read_text(encoding="utf-8"))
        # PyYAML resolves the bare `on:` key to the boolean True.
        triggers = document[True] if True in document else document["on"]
        self.assertIn("push", triggers)
        self.assertIsNone(
            triggers["push"],
            "on.push must carry no filters; branch selection is the "
            "organization ruleset's, not Core's",
        )

    def test_push_evaluation_is_serialized_but_never_cancelled(self) -> None:
        """A canonical evaluation of an immutable SHA must complete.

        Cancelling in-progress runs would let a later event revoke an
        in-flight attestation of a revision that a birth or release record may
        already cite. The group is keyed by repository, revision, and event
        family so a push evaluation and a pull_request evaluation of the same
        tree never collapse into one another.
        """
        document = yaml.safe_load(ENTRYPOINT_PATH.read_text(encoding="utf-8"))
        concurrency = document["concurrency"]
        self.assertFalse(concurrency["cancel-in-progress"])
        for expression in (
            "github.repository",
            "github.sha",
            "github.event_name",
        ):
            self.assertIn(expression, concurrency["group"])

    def test_every_allowed_event_class_resolves_to_a_profile_that_permits_it(
        self,
    ) -> None:
        """A `workflow_call` caller may omit `profile`; the fallback must resolve.

        The dispatcher falls back to `fixed.get(event_class, ... or event_class)`,
        so an event class with no entry in `fixed` becomes a profile name. That
        is only correct when a profile of that name exists *and* declares the
        event. `push` has no same-named profile — `merge` is the profile whose
        `allowed_events` contains it — so omitting the mapping made
        `resolve-governance` abort with `unknown execution profile`.
        """
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        allowed = set(
            re.search(r"allowed = \{([^}]*)\}", text)
            .group(1)
            .replace('"', "")
            .split(", ")
        )
        fixed = dict(
            pair.split(": ")
            for pair in re.search(r"fixed = \{([^}]*)\}", text)
            .group(1)
            .replace('"', "")
            .split(", ")
        )
        profiles = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))["profiles"]

        for event_class in sorted(allowed):
            profile_name = fixed.get(event_class, event_class)
            self.assertIn(
                profile_name,
                profiles,
                f"event class {event_class!r} resolves to unknown profile "
                f"{profile_name!r}",
            )
            self.assertIn(
                event_class,
                profiles[profile_name]["allowed_events"],
                f"profile {profile_name!r} does not allow event {event_class!r}",
            )

    def test_entrypoint_does_not_accept_consumer_governance_or_language_authority(
        self,
    ) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        header = text.split("permissions:", 1)[0]
        self.assertNotRegex(header, r"(?m)^\s{6}governance:\s*$")
        self.assertNotRegex(header, r"(?m)^\s{6}language:\s*$")
        self.assertNotIn(".github/governance", text)
        self.assertNotIn(".github/org-governance-defaults", text)
        self.assertIn('governance-root: "@core-defaults"', text)

    def test_entrypoint_composes_only_full_sha_core_primitives(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        actions = (
            "resolve-consumer-metadata",
            "resolve-governance",
            "provision-sdk",
            "invoke-sdk",
            "validate-bundle",
            "route-artifacts",
            "build-artifact-manifest",
        )
        pins: set[str] = set()
        for action in actions:
            match = re.search(
                rf"Quantum-L9/l9-ci-core/\.github/actions/{re.escape(action)}@([0-9a-f]{{40}})",
                text,
            )
            self.assertIsNotNone(match, action)
            assert match is not None
            pins.add(match.group(1))
        self.assertEqual(1, len(pins), pins)
        self.assertNotIn("publish-analysis.yml@", text)

    def test_sdk_owns_capability_detection(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        self.assertIn("providers detect --root . --format json", text)
        self.assertIn(
            "consumer repo_class=python conflicts with SDK capability detection", text
        )
        self.assertIn(
            "consumer repo_class=typescript conflicts with SDK capability detection",
            text,
        )
        self.assertIn("SDK capability detection is ambiguous", text)

    def test_blocking_gate_summary_precedes_enforcement(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        summary = text.index("name: Write central CI summary")
        enforce = text.index("name: Enforce central mode on SDK technical gate")
        self.assertLess(summary, enforce)
        self.assertIn("if: always()", text[summary:enforce])

    def test_required_workflow_has_no_write_scopes(self) -> None:
        text = ENTRYPOINT_PATH.read_text(encoding="utf-8")
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        self.assertEqual([], write_pattern.findall(text))

    def test_consumer_schema_is_descriptive_only(self) -> None:
        import json

        schema = json.loads(CONSUMER_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema", "owner", "repo_class", "waiver_refs"},
            set(schema["properties"]),
        )
        self.assertFalse(schema["additionalProperties"])

    def test_contract_explicitly_prohibits_distribution_architecture(self) -> None:
        contract = load_contract()
        prohibited = set(contract["ownership"]["prohibited"])
        self.assertIn("CI distribution from Quantum-L9/.github", prohibited)
        self.assertIn(
            "copied L9 workflows in consumer repositories as an enforcement mechanism",
            prohibited,
        )
        self.assertIn("copied L9 governance packs in consumer repositories", prohibited)
        self.assertIn("a second organization CI control-plane repository", prohibited)
        self.assertIn(
            "write-scoped publication from the pull_request required-workflow path",
            prohibited,
        )

    def test_internal_and_sdk_pinning_fail_closed(self) -> None:
        contract = load_contract()
        self.assertEqual(
            "full-40-char-sha",
            contract["pinning"]["core_internal_actions"]["policy"],
        )
        self.assertFalse(
            contract["pinning"]["core_internal_actions"]["floating_references_allowed"]
        )
        sdk = contract["pinning"]["sdk_revision"]
        self.assertFalse(sdk["floating_git_references_allowed"])
        self.assertFalse(sdk["branches_allowed"])
        self.assertFalse(sdk["tags_allowed"])
        self.assertFalse(sdk["short_git_revisions_allowed"])


if __name__ == "__main__":
    unittest.main()
