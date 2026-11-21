"""The central finding-waiver ledger Core ships and stages for the SDK."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/resolve-governance"
MODULE_PATH = ACTION / "resolve.py"
spec = importlib.util.spec_from_file_location("resolve_finding_waivers", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

LEDGER = ACTION / "finding-waivers.yaml"
ENTRY_KEYS = {
    "id",
    "owner",
    "reason",
    "created",
    "expires",
    "scope",
    "fingerprint",
    "source_sha256",
}
SCOPE_KEYS = {"repository", "provider_id", "provider_rule_id", "path"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")


class ShippedLedgerTests(unittest.TestCase):
    """Every entry is keyed on one exact finding, never on a rule or a line."""

    def setUp(self) -> None:
        self.document = json.loads(LEDGER.read_text(encoding="utf-8"))

    def test_ledger_sits_next_to_the_sdk_policy(self) -> None:
        self.assertEqual(LEDGER, module.finding_waivers_path())
        self.assertTrue((ACTION / "semgrep-policy.yaml").is_file())

    def test_envelope_is_valid(self) -> None:
        self.assertEqual(LEDGER.read_bytes(), module.validated_finding_waivers(LEDGER))
        self.assertEqual("l9.finding-waivers/v1", self.document["schema"])

    def test_every_entry_names_one_finding_exactly(self) -> None:
        for entry in self.document["waivers"]:
            with self.subTest(waiver=entry.get("id")):
                self.assertEqual(ENTRY_KEYS, set(entry))
                self.assertEqual(SCOPE_KEYS, set(entry["scope"]))
                self.assertRegex(entry["scope"]["repository"], REPOSITORY)
                self.assertFalse(entry["scope"]["path"].startswith(("/", "./")))
                self.assertRegex(entry["fingerprint"], HEX64)
                self.assertRegex(entry["source_sha256"], HEX64)
                self.assertTrue(entry["owner"].strip())
                self.assertTrue(entry["reason"].strip())

    def test_every_entry_expires(self) -> None:
        for entry in self.document["waivers"]:
            with self.subTest(waiver=entry["id"]):
                created = dt.date.fromisoformat(entry["created"])
                expires = dt.date.fromisoformat(entry["expires"])
                self.assertLessEqual(created, expires)

    def test_ledger_is_part_of_the_governance_digest(self) -> None:
        root = module.core_defaults_path()
        baseline = module.canonical_digest(root, None)
        self.assertEqual(
            baseline,
            module.canonical_digest(root, None, finding_waivers=LEDGER.read_bytes()),
        )
        self.assertNotEqual(
            baseline,
            module.canonical_digest(
                root, None, finding_waivers=LEDGER.read_bytes() + b"\n"
            ),
        )


SAMPLE_ENTRY = {
    "id": "SAMPLE-ENTRY",
    "owner": "L1",
    "reason": "sample",
    "created": "2026-11-21",
    "expires": "2027-02-21",
    "scope": {
        "repository": "Quantum-L9/l9-ci-sdk",
        "provider_id": "semgrep",
        "provider_rule_id": "python.example.rule",
        "path": "l9_ci/example.py",
    },
    "fingerprint": "0" * 64,
    "source_sha256": "1" * 64,
}


class EnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "finding-waivers.yaml"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def refused(self, document: object) -> None:
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(module.GovernanceError):
            module.validated_finding_waivers(self.path)

    def test_well_formed_ledger_with_an_entry_is_accepted(self) -> None:
        document = {"schema": "l9.finding-waivers/v1", "waivers": [SAMPLE_ENTRY]}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(
            self.path.read_bytes(), module.validated_finding_waivers(self.path)
        )

    def test_malformed_ledgers_fail_closed(self) -> None:
        schema = "l9.finding-waivers/v1"
        for document in (
            [],
            {"schema": schema},
            {"schema": "l9.waivers/v1", "waivers": []},
            {"schema": schema, "waivers": {}},
            {"schema": schema, "waivers": [], "extra": 1},
            {"schema": schema, "waivers": ["entry"]},
            {"schema": schema, "waivers": [{"id": ""}]},
            {"schema": schema, "waivers": [{"id": "A"}, {"id": "A"}]},
        ):
            with self.subTest(document=document):
                self.refused(document)

    def test_non_json_ledger_fails_closed(self) -> None:
        self.path.write_text("schema: [", encoding="utf-8")
        with self.assertRaises(module.GovernanceError):
            module.validated_finding_waivers(self.path)

    def test_missing_ledger_fails_closed(self) -> None:
        with self.assertRaises(module.GovernanceError):
            module.validated_finding_waivers(self.path)


class StagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.workspace = base / "workspace"
        self.workspace.mkdir()
        self.runner_temp = base / "runner-temp"
        self.runner_temp.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ledger_is_staged_outside_the_workspace(self) -> None:
        environment = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "RUNNER_TEMP": str(self.runner_temp),
        }
        with patch.dict(os.environ, environment, clear=True):
            staged = Path(module.stage_finding_waivers(b"payload"))
        self.assertTrue(staged.is_absolute())
        self.assertTrue(staged.is_relative_to(self.runner_temp))
        self.assertFalse(staged.is_relative_to(self.workspace))
        self.assertEqual(b"payload", staged.read_bytes())

    def test_staging_inside_the_workspace_is_refused(self) -> None:
        environment = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "RUNNER_TEMP": str(self.workspace),
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(module.GovernanceError):
                module.stage_finding_waivers(b"payload")

    def test_action_exposes_the_staged_ledger(self) -> None:
        action = (ACTION / "action.yml").read_text(encoding="utf-8")
        self.assertIn("value: ${{ steps.resolve.outputs.finding-waivers }}", action)


if __name__ == "__main__":
    unittest.main()
