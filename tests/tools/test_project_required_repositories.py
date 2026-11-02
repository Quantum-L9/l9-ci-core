"""required_repositories is a projection of the Quantum-L9/.github registry."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import project_required_repositories as projection  # noqa: E402

COMMIT = "1" * 40


def _registry(*entries: tuple[str, str, str]) -> dict:
    return {
        "schema": "l9.repository-registry/v1",
        "artifact_id": "l9.repository-registry/global@1",
        "canonical": True,
        "repositories": [
            {
                "id": entry_id,
                "coordinate": {
                    "provider": "github",
                    "organization": "Quantum-L9",
                    "repository": name,
                },
                "lifecycle": lifecycle,
                "class_ref": "l9.repository-class/l9@1",
            }
            for entry_id, name, lifecycle in entries
        ],
    }


def _load_verifier():
    path = ROOT / ".github" / "actions" / "run-repository-verification" / "run.py"
    spec = importlib.util.spec_from_file_location(
        "l9_repository_verify_projection", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectionRuleTests(unittest.TestCase):
    def test_only_current_members_are_required_in_canonical_order(self) -> None:
        registry = _registry(
            ("zeta", "zeta", "current"),
            ("alpha", "Alpha", "current"),
            ("old", "old-name", "superseded"),
            ("gone", "gone", "retired"),
        )
        self.assertEqual(
            ["Quantum-L9/Alpha", "Quantum-L9/zeta"], projection.project(registry)
        )

    def test_invalid_sources_fail_closed(self) -> None:
        base = _registry(("a", "a", "current"))
        cases = {
            "schema": lambda r: r.update(schema="l9.repository-registry/v2"),
            "canonical": lambda r: r.update(canonical=False),
            "empty": lambda r: r.update(repositories=[]),
            "duplicate id": lambda r: r["repositories"].append(
                copy.deepcopy(r["repositories"][0])
            ),
            "provider": lambda r: r["repositories"][0]["coordinate"].update(
                provider="gitlab"
            ),
            "class_ref": lambda r: r["repositories"][0].pop("class_ref"),
            "lifecycle": lambda r: r["repositories"][0].update(lifecycle="archived"),
            "name": lambda r: r["repositories"][0]["coordinate"].update(
                repository="a/b"
            ),
        }
        for label, mutate in cases.items():
            registry = copy.deepcopy(base)
            mutate(registry)
            with (
                self.subTest(case=label),
                self.assertRaises(projection.ProjectionError),
            ):
                projection.project(registry)

    def test_coordinates_differing_only_in_case_are_one_repository(self) -> None:
        registry = _registry(("a", "Repo", "current"), ("b", "repo", "current"))
        with self.assertRaisesRegex(projection.ProjectionError, "duplicated"):
            projection.project(registry)

    def test_no_current_member_is_an_error(self) -> None:
        with self.assertRaisesRegex(projection.ProjectionError, "no current"):
            projection.project(_registry(("gone", "gone", "retired")))

    def test_render_keeps_policy_fields_and_binds_the_source(self) -> None:
        policy = {
            "schema": "l9.repository-contract-enforcement/v1",
            "default_mode": "migration",
            "required_repositories": [],
        }
        policy_text, receipt_text = projection.render(
            ["Quantum-L9/a"], policy, b"registry bytes", COMMIT
        )
        self.assertEqual(
            {**policy, "required_repositories": ["Quantum-L9/a"]},
            json.loads(policy_text),
        )
        receipt = json.loads(receipt_text)
        self.assertEqual(COMMIT, receipt["source"]["commit"])
        self.assertEqual(
            projection.members_digest(["Quantum-L9/a"]), receipt["members_sha256"]
        )
        with self.assertRaises(projection.ProjectionError):
            projection.render(["Quantum-L9/a"], policy, b"x", "main")


class ShippedProjectionTests(unittest.TestCase):
    def test_shipped_policy_matches_its_receipt(self) -> None:
        self.assertEqual([], projection.verify())

    def test_verify_catches_a_hand_edited_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "contract-policy.json"
            receipt = Path(tmp) / "receipt.json"
            data = json.loads(projection.POLICY.read_text(encoding="utf-8"))
            data["required_repositories"] = data["required_repositories"][1:]
            policy.write_text(json.dumps(data), encoding="utf-8")
            receipt.write_text(
                projection.RECEIPT.read_text(encoding="utf-8"), encoding="utf-8"
            )
            with (
                unittest.mock.patch.object(projection, "POLICY", policy),
                unittest.mock.patch.object(projection, "RECEIPT", receipt),
            ):
                self.assertIn(
                    "policy required_repositories differs from the receipt's projected members",
                    projection.verify(),
                )

    def test_verifier_requires_every_projected_member_and_nothing_else(self) -> None:
        verifier = _load_verifier()
        policy = verifier.load_policy()
        members = json.loads(projection.RECEIPT.read_text(encoding="utf-8"))["members"]
        self.assertIn("Quantum-L9/l9-ci-core", members)
        for member in members:
            with self.subTest(member=member):
                self.assertEqual(
                    verifier.REQUIRED_MODE, verifier.resolve_mode(policy, member)
                )
        self.assertEqual(
            verifier.MIGRATION_MODE,
            verifier.resolve_mode(policy, "Quantum-L9/not-a-member"),
        )


if __name__ == "__main__":
    unittest.main()
