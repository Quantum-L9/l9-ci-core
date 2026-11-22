"""Witnesses for the proposed ``ci_runtime_invariants`` (L9-CI-CORE-001..006).

One known-good and one known-bad witness per invariant, named by ID. The
statements are Proposed (``docs/adr/``); a passing witness proves the gate
logic it names and nothing about admission, rollout or a hosted run.

The scheduling half of ``L9-CI-CORE-003`` is witnessed against
central-semgrep.yml, the Semgrep chain the nightly scan runs since it left the
required check (docs/semgrep-runs-nightly.md; AGENTS.md §35), with a
known-bad single-language variant.
``L9-CI-CORE-004`` and ``L9-CI-CORE-006`` prove gate logic only; no live canary, ruleset read or
hosted run observation has been made by these tests.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
# Core's tools/ is not a package, and under `unittest discover --start-directory
# tests` the name `tools` resolves to tests/tools. Import the module the way the
# other suites import from tools/ (Repo.mk test-deep).
sys.path.insert(0, str(ROOT / "tools"))

import ci_runtime_invariants as invariants  # noqa: E402

ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
SCAN = ROOT / ".github" / "workflows" / "central-semgrep.yml"
METADATA = ROOT / ".github" / "actions" / "resolve-consumer-metadata" / "metadata.py"
ADR_DIR = ROOT / "docs" / "adr"

spec = importlib.util.spec_from_file_location("resolve_consumer_metadata", METADATA)
assert spec and spec.loader
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)

sys.path.insert(0, str(ROOT / "tools"))
from l9_repo import inherited_config  # noqa: E402
from l9_repo import stamp as l9_stamp  # noqa: E402

INSTANCE_FIXTURE = ROOT / "tests" / "fixtures" / "required-instances" / "mixed"
AGGREGATE = (
    ROOT / ".github" / "actions" / "aggregate-required-instances" / "aggregate.py"
)
aggregate_spec = importlib.util.spec_from_file_location(
    "aggregate_required_instances", AGGREGATE
)
assert aggregate_spec and aggregate_spec.loader
aggregator = importlib.util.module_from_spec(aggregate_spec)
sys.modules[aggregate_spec.name] = aggregator
aggregate_spec.loader.exec_module(aggregator)

CORE = "1" * 40
SDK = "2" * 40
ROLLBACK = "3" * 40
HEAD = "4" * 40
OTHER_HEAD = "5" * 40
CONFIG = "sha256:" + "6" * 64
OLD_CONFIG = "sha256:" + "7" * 64
REQUIRED_CHECK = yaml.safe_load(
    (ROOT / ".l9" / "org-runtime-contract.yaml").read_text(encoding="utf-8")
)["entrypoint"]["required_check"]["name"]


def load_consumer_metadata(payload: dict[str, Any]) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / ".l9" / "ci.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return metadata.load_metadata(path)


def aggregate_fixture(typescript: str = "pass") -> dict[str, Any]:
    """Aggregate the Python plus TypeScript fixture, optionally breaking one."""
    plan = json.loads((INSTANCE_FIXTURE / "plan.json").read_text(encoding="utf-8"))
    records = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((INSTANCE_FIXTURE / "results").glob("*.json"))
    }
    name = "semgrep-typescript.json"
    if typescript == "removed":
        del records[name]
    elif typescript == "untrusted":
        records[name]["revision"] = OTHER_HEAD
    else:
        records[name]["status"] = typescript
    return aggregator.aggregate(aggregator.parse_plan(plan), list(records.items()))


def proven_retirement() -> dict[str, Any]:
    promoted = {
        "core_revision": CORE,
        "sdk_revision": SDK,
        "configuration_digest": CONFIG,
    }
    return {
        **promoted,
        "rollback_core_revision": ROLLBACK,
        "known_bad": {**promoted, "outcome": "fail"},
        "known_good": {**promoted, "outcome": "pass"},
        "canary": {
            **promoted,
            "status": invariants.CANARY_PASS,
            "run_url": "https://github.com/Quantum-L9/Cursor-Governance/actions/runs/1",
        },
        "parity": {**promoted, "integrated": True, "consumer_revision": HEAD},
    }


def enforcement_record(owner: str) -> dict[str, Any]:
    return {
        "ruleset_owner": owner,
        "ruleset_id": "21895545",
        "enforcement": "active",
        "target_branch": "main",
        "bypass_actors": [],
        "required_check": REQUIRED_CHECK,
        "observed_check": REQUIRED_CHECK,
        "observed_conclusion": "success",
        "evaluated_head": HEAD,
        "observed_run_head": HEAD,
        "observed_run_url": f"https://github.com/{owner}/example/actions/runs/1",
    }


class ContractClosureTests(unittest.TestCase):
    """The contract, the ADR binding and this module name the same six IDs."""

    EXPECTED = tuple(f"L9-CI-CORE-00{number}" for number in range(1, 7))

    def test_contract_declares_exactly_the_six_proposed_invariants(self) -> None:
        self.assertEqual(self.EXPECTED, invariants.contract_invariant_ids())

    def test_every_invariant_has_a_known_good_and_a_known_bad_witness(self) -> None:
        names = [
            name
            for case in (RuntimeInvariantWitnessTests,)
            for name in dir(case)
            if name.startswith("test_")
        ]
        for identifier in invariants.contract_invariant_ids():
            token = identifier.replace("-", "_")
            with self.subTest(invariant=identifier):
                self.assertTrue(
                    [n for n in names if f"{token}_known_good" in n],
                    f"{identifier} has no known-good witness",
                )
                self.assertTrue(
                    [n for n in names if f"{token}_known_bad" in n],
                    f"{identifier} has no known-bad witness",
                )

    def test_adrs_are_proposed_and_bind_every_invariant(self) -> None:
        index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
        records = sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))
        self.assertEqual(2, len(records))
        bound: set[str] = set()
        for record in records:
            text = record.read_text(encoding="utf-8")
            with self.subTest(adr=record.name):
                self.assertIn(f"]({record.name})", index)
                status = text.split("## Status", 1)[1].split("##", 1)[0]
                self.assertTrue(status.strip().startswith("Proposed."))
                self.assertNotIn("Accepted", status)
            related = text.split("**Related:**", 1)[1].split("\n", 1)[0]
            prefix = re.search(r"`(L9-CI-CORE-)(\d{3})`", related)
            assert prefix is not None
            bound.add(prefix.group(1) + prefix.group(2))
            tail = related[prefix.end() :].split(";", 1)[0]
            bound.update(
                f"L9-CI-CORE-{number}" for number in re.findall(r"`(\d{3})`", tail)
            )
        self.assertEqual(set(self.EXPECTED), bound)
        self.assertNotIn("| Accepted |", index)

    def test_no_test_imports_core_tools_as_a_dotted_package(self) -> None:
        """Under test-deep discover, `tools` is tests/tools, not Core's tools/."""
        pattern = re.compile(r"^\s*(from tools[ .]|import tools\b)", re.MULTILINE)
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in sorted((ROOT / "tests").rglob("*.py"))
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)
        self.assertFalse((ROOT / "tools" / "__init__.py").exists())

    def test_capability_probes_read_core_paths_not_dotted_imports(self) -> None:
        text = (ROOT / "tools" / "ci_runtime_invariants.py").read_text("utf-8")
        self.assertNotIn("import_module", text)
        self.assertNotIn('"tools.', text)

    def test_unknown_capability_is_never_treated_as_present(self) -> None:
        with self.assertRaises(invariants.MissingCapability):
            invariants.require_capability("not-a-capability")


class RuntimeInvariantWitnessTests(unittest.TestCase):
    # -- L9-CI-CORE-001: central control is non-delegable -------------------
    def test_L9_CI_CORE_001_known_good_product_only_declaration_is_accepted(
        self,
    ) -> None:
        loaded = load_consumer_metadata(
            {
                "schema": "l9.ci-consumer/v1",
                "owner": "Quantum-L9/platform",
                "repo_class": "python",
                "product_kind": "node",
            }
        )
        assert loaded is not None
        self.assertEqual("node", loaded["product_kind"])

    def test_L9_CI_CORE_001_known_bad_consumer_override_is_refused(self) -> None:
        overrides = {
            "mode": "advisory",
            "required": False,
            "enabled": False,
            "semgrep_version": "0.0.1",
            "sdk_revision": "0" * 40,
            "core_revision": "0" * 40,
            "ignore": ["**"],
            "permissions": {"contents": "write"},
        }
        for key, value in overrides.items():
            with self.subTest(override=key):
                with self.assertRaises(metadata.ConsumerMetadataError) as raised:
                    load_consumer_metadata({"schema": "l9.ci-consumer/v1", key: value})
                self.assertIn("forbidden keys", str(raised.exception))
                self.assertIn(key, str(raised.exception))

    # -- L9-CI-CORE-002: inherited configuration without stamp churn --------
    CAPABILITY_002 = "inherited-configuration-digest"

    def stamped_without_copies(self, root: Path) -> None:
        (root / "Repo.mk").write_bytes(
            b".PHONY: repo-setup repo-validate repo-check repo-test\n"
            b"repo-setup repo-validate repo-check repo-test:\n\t@true\n"
        )
        l9_stamp.stamp(root, "python+typescript", CORE, ["all"])

    def test_L9_CI_CORE_002_known_good_repository_inherits_without_copies(
        self,
    ) -> None:
        invariants.require_capability(self.CAPABILITY_002)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.stamped_without_copies(root)
            for relative in l9_stamp.inheritable_files("python+typescript"):
                self.assertFalse((root / relative).exists(), relative)
            self.assertEqual([], l9_stamp.verify_stamp(root)[1])
            first = inherited_config.resolve(root)
            second = inherited_config.resolve(root)
            assert first is not None and second is not None
            self.assertRegex(first.configuration_digest, r"^sha256:[0-9a-f]{64}$")
            # Identical evaluated revision, identical resolved configuration.
            self.assertEqual(first.configuration_digest, second.configuration_digest)

    def test_L9_CI_CORE_002_known_bad_consumer_override_is_refused(self) -> None:
        invariants.require_capability(self.CAPABILITY_002)
        overrides = {
            "version": ("requirements-ci.txt", b"ruff==0.0.1\n"),
            "mode": ("mypy.ini", b"[mypy]\nstrict = False\n"),
            "ignore scope": (".gitleaks.toml", b'[allowlist]\npaths = [".*"]\n'),
        }
        for name, (relative, data) in overrides.items():
            with self.subTest(override=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.stamped_without_copies(root)
                (root / relative).write_bytes(data)
                findings = l9_stamp.verify_stamp(root)[1]
                self.assertEqual(1, len(findings))
                self.assertIn(f"{relative}: local copy overrides", findings[0])
                self.assertIn("central sha256:", findings[0])
                self.assertIn("local sha256:", findings[0])

    # -- L9-CI-CORE-003: all required applicable checks conclude truthfully -
    # The aggregate decision is witnessed on the mixed-language fixture, and
    # the required workflow plans, runs and aggregates every instance
    # (AGENTS.md §35).
    def test_L9_CI_CORE_003_known_good_mixed_language_aggregate_passes(self) -> None:
        invariants.require_capability("required-instance-aggregate")
        result = aggregate_fixture()
        self.assertEqual("pass", result["status"])
        self.assertEqual(2, result["completed"])
        self.assertEqual(
            ["semgrep:language:python", "semgrep:language:typescript"],
            [row["id"] for row in result["instances"]],
        )

    def test_L9_CI_CORE_003_known_bad_unaccounted_instance_is_never_a_pass(
        self,
    ) -> None:
        invariants.require_capability("required-instance-aggregate")
        for case in ("removed", "skipped", "timed_out", "untrusted"):
            with self.subTest(case=case):
                result = aggregate_fixture(typescript=case)
                self.assertEqual("incomplete", result["status"])
                self.assertFalse(result["accounted"])
                self.assertEqual(6, result["exit_code"])

    def test_L9_CI_CORE_003_known_good_required_job_schedules_every_instance(
        self,
    ) -> None:
        """The Semgrep scan plans, runs, aggregates and enforces every instance."""
        invariants.require_capability("required-instance-scheduling")
        text = SCAN.read_text(encoding="utf-8")
        self.assertIn("plan-required-instances@", text)
        self.assertIn("aggregate-required-instances@", text)

    def test_L9_CI_CORE_003_known_bad_single_language_job_fails_closed(
        self,
    ) -> None:
        """The pre-§35 scan (one detected language) lacks the capability."""
        text = SCAN.read_text(encoding="utf-8")
        for marker in (
            "aggregate-required-instances@",
            "plan-required-instances@",
            "providers inventory",
            "AGGREGATE_STATUS: ${{ steps.aggregate.outputs.status }}",
        ):
            with self.subTest(removed=marker):
                self.assertFalse(
                    invariants._schedules_every_instance(text.replace(marker, "x"))
                )

    # -- L9-CI-CORE-004: central promotion precedes consumer retirement -----
    def test_L9_CI_CORE_004_known_good_proven_successor_allows_retirement(
        self,
    ) -> None:
        decision = invariants.retirement_decision(proven_retirement())
        self.assertEqual((), decision.reasons)
        self.assertTrue(decision.allowed)

    def test_L9_CI_CORE_004_known_bad_unproven_successor_refuses_retirement(
        self,
    ) -> None:
        def without(key: str) -> dict[str, Any]:
            evidence = proven_retirement()
            del evidence[key]
            return evidence

        def changed(record: str, **changes: Any) -> dict[str, Any]:
            evidence = proven_retirement()
            evidence[record] = {**evidence[record], **changes}
            return evidence

        cases = {
            "static similarity only": {"core_revision": CORE, "sdk_revision": SDK},
            "no canary": without("canary"),
            "no parity": without("parity"),
            "no known-bad proof": without("known_bad"),
            "no known-good proof": without("known_good"),
            "no rollback revision": without("rollback_core_revision"),
            "no configuration digest": without("configuration_digest"),
            "known-bad did not fail": changed("known_bad", outcome="pass"),
            "known-good from an older SDK": changed("known_good", sdk_revision=HEAD),
            "known-bad from an older Core": changed("known_bad", core_revision=HEAD),
            "known-bad under another configuration": changed(
                "known_bad", configuration_digest=OLD_CONFIG
            ),
            "parity under another configuration": changed(
                "parity", configuration_digest=OLD_CONFIG
            ),
            "parity from an older Core": changed("parity", core_revision=HEAD),
            "canary blocked": changed("canary", status="CURSOR_CANARY_BLOCKED"),
            "canary on another SDK": changed("canary", sdk_revision=OTHER_HEAD),
            "canary without a run": changed("canary", run_url=""),
            "parity from a unit test": changed("parity", integrated=False),
            "bare booleans instead of bound records": {
                **proven_retirement(),
                "known_bad": True,
                "known_good": True,
            },
            "empty": {},
        }
        for name, evidence in cases.items():
            with self.subTest(case=name):
                decision = invariants.retirement_decision(evidence)
                self.assertFalse(decision.allowed)
                self.assertTrue(decision.reasons)

    # -- L9-CI-CORE-005: native privileged scanners keep trust boundaries ---
    def test_L9_CI_CORE_005_known_good_required_workflow_is_read_only(self) -> None:
        text = ORG_CI.read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertEqual([], invariants.trust_boundary_violations(text))

    def test_L9_CI_CORE_005_known_bad_privileged_scope_is_reported(self) -> None:
        text = ORG_CI.read_text(encoding="utf-8")
        job_permissions = "    permissions:\n      contents: read\n"
        # One per job: gitleaks, pip-audit, npm-audit, CodeQL, actionlint,
        # Biome and analyze, each a step job; each mutation widens every one.
        self.assertEqual(7, text.count(job_permissions))
        token = "${{ secrets.L9_GITHUB_READ_TOKEN }}"
        self.assertIn(token, text)
        cases = {
            "security-events write": text.replace(
                job_permissions, job_permissions + "      security-events: write\n"
            ),
            "contents write": text.replace(
                job_permissions, "    permissions:\n      contents: write\n"
            ),
            "write-all": text.replace(
                "permissions:\n  contents: read\n", "permissions: write-all\n", 1
            ),
            "vendor token": text.replace(token, "${{ secrets.SONAR_TOKEN }}", 1),
            "inherited secrets": text.replace(
                job_permissions, job_permissions + "    secrets: inherit\n"
            ),
        }
        for name, mutated in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(text, mutated)
                self.assertTrue(invariants.trust_boundary_violations(mutated))

    # -- L9-CI-CORE-006: coverage is proved at the enforcement boundary -----
    def test_L9_CI_CORE_006_known_good_each_owner_has_its_own_evidence(self) -> None:
        decision = invariants.coverage_claim(
            ["Quantum-L9/example", "cryptoxdog/IB-Odoo_19"],
            {
                "Quantum-L9/example": enforcement_record("Quantum-L9"),
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
        )
        self.assertEqual((), decision.reasons)
        self.assertTrue(decision.allowed)

    def test_L9_CI_CORE_006_known_bad_coverage_by_implication_is_refused(
        self,
    ) -> None:
        org = enforcement_record("Quantum-L9")
        cases: dict[str, dict[str, dict[str, Any]]] = {
            "other owner has no evidence": {"Quantum-L9/example": org},
            "org ruleset claimed for another owner": {
                "Quantum-L9/example": org,
                "cryptoxdog/IB-Odoo_19": enforcement_record("Quantum-L9"),
            },
            "stale required check": {
                "Quantum-L9/example": org,
                "cryptoxdog/IB-Odoo_19": {
                    **enforcement_record("cryptoxdog"),
                    "observed_run_head": OTHER_HEAD,
                },
            },
            "evaluate-mode ruleset": {
                "Quantum-L9/example": {**org, "enforcement": "evaluate"},
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
            "unrelated workflow run on the same head": {
                "Quantum-L9/example": {**org, "observed_check": "Core Sonar analysis"},
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
            "required check failed": {
                "Quantum-L9/example": {**org, "observed_conclusion": "failure"},
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
            "required check cancelled": {
                "Quantum-L9/example": {**org, "observed_conclusion": "cancelled"},
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
            "required check unknown": {
                "Quantum-L9/example": {**org, "required_check": ""},
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
            "ruleset unidentified": {
                "Quantum-L9/example": {**org, "ruleset_id": ""},
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
            "unknown bypass policy": {
                "Quantum-L9/example": {**org, "bypass_actors": None},
                "cryptoxdog/IB-Odoo_19": enforcement_record("cryptoxdog"),
            },
        }
        for name, evidence in cases.items():
            with self.subTest(case=name):
                decision = invariants.coverage_claim(
                    ["Quantum-L9/example", "cryptoxdog/IB-Odoo_19"], evidence
                )
                self.assertFalse(decision.allowed)
                self.assertTrue(decision.reasons)
        self.assertFalse(invariants.coverage_claim([], {}).allowed)


if __name__ == "__main__":
    unittest.main()
