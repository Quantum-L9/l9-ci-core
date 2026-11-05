"""Contract tests for the locked consumer Biome contract.

Biome owns JS/TS/JSON format + lint. Core ships exactly one consumer-facing
Biome artifact: the locked ``biome.json`` contract plus the ``stamp.sh`` that
copies it into a consumer without inventing configuration. The copy-first
preset and starter workflows that once called the SDK Biome reusable workflow
were retired on 2026-10-22 (``AGENTS.md`` §15); the organization workflow
runs Core's own ``install-biome`` / ``run-biome-check`` instead.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRESET_DIR = ROOT / "presets" / "typescript"
PRESET_README = PRESET_DIR / "README.md"
STAMP = PRESET_DIR / "stamp.sh"
LOCKED_BIOME = PRESET_DIR / "biome.json"


class LockedBiomeContractTests(unittest.TestCase):
    def test_no_preset_workflow_or_governance_pack_is_shipped(self) -> None:
        self.assertFalse((PRESET_DIR / ".github").exists())
        self.assertFalse((ROOT / "presets" / "python").exists())
        self.assertEqual(
            {
                ".biomeignore",
                ".editorconfig",
                ".vscode",
                "README.md",
                "biome.json",
                "stamp.sh",
            },
            {path.name for path in PRESET_DIR.iterdir()},
        )

    def test_preset_ships_locked_biome_json(self) -> None:
        self.assertTrue(LOCKED_BIOME.is_file(), "presets/typescript/biome.json missing")
        contract = json.loads(LOCKED_BIOME.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["$schema"], "https://biomejs.dev/schemas/2.5.8/schema.json"
        )
        self.assertTrue(contract["formatter"]["enabled"])
        self.assertTrue(contract["linter"]["enabled"])
        self.assertIn("javascript", contract)
        self.assertIn("json", contract)
        includes = contract["files"]["includes"]
        self.assertIn("**", includes)
        self.assertIn("!**/node_modules", includes)
        self.assertNotIn(
            "!**/website_pack/generated",
            includes,
            "locked contract must stay consumer-generic",
        )

    def test_readme_stamps_and_forbids_hand_authoring(self) -> None:
        readme = PRESET_README.read_text(encoding="utf-8")
        self.assertIn("stamp.sh", readme)
        self.assertIn("do not hand-author", readme.lower())
        self.assertNotIn(
            "npx eslint", readme, "README still routes lint through ESLint"
        )
        self.assertNotIn("l9-biome-scan", readme)
        self.assertNotIn("cp -r", readme, "README must stamp, not copy workflows")

    def test_stamp_script_copies_when_absent_and_keeps_existing(self) -> None:
        self.assertTrue(STAMP.is_file(), "presets/typescript/stamp.sh missing")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = subprocess.run(
                ["bash", str(STAMP), str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("stamped biome.json", first.stdout)
            stamped = (target / "biome.json").read_text(encoding="utf-8")
            self.assertEqual(stamped, LOCKED_BIOME.read_text(encoding="utf-8"))
            self.assertTrue((target / ".biomeignore").is_file())
            self.assertTrue((target / ".editorconfig").is_file())
            recs = json.loads((target / ".vscode" / "extensions.json").read_text())
            self.assertIn("biomejs.biome", recs["recommendations"])

            (target / "biome.json").write_text('{"root": false}\n', encoding="utf-8")
            second = subprocess.run(
                ["bash", str(STAMP), str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("keep existing biome.json", second.stdout)
            self.assertEqual(
                (target / "biome.json").read_text(encoding="utf-8"), '{"root": false}\n'
            )


if __name__ == "__main__":
    unittest.main()
