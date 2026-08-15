"""Contract tests for the TypeScript preset's convergence onto the SDK-owned
Biome reusable workflow.

Biome (via ``l9-ci-sdk/.github/workflows/l9-biome-scan.yml``) owns JS/TS/JSON
format + lint; the preset must invoke it by a full-SHA pin, keep ``tsc`` type
checking and the test suite, and never reintroduce a second formatter owner or
copy the SDK workflow implementation into Core.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PRESET_DIR = ROOT / "presets" / "typescript"
PRESET = PRESET_DIR / ".github" / "workflows" / "l9-lint-test.yml"
STARTER = ROOT / "starter-workflows" / "typescript" / "l9-lint-test.yml"
LINT_TEST_FILES = (PRESET, STARTER)
COMPAT_MANIFEST = ROOT / ".l9" / "sdk-compatibility.yaml"
ACTIVATION_SKILL = ROOT / "skills" / "l9-ci-activation-typescript" / "SKILL.md"
PRESET_README = PRESET_DIR / "README.md"
STAMP = PRESET_DIR / "stamp.sh"
LOCKED_BIOME = PRESET_DIR / "biome.json"

BIOME_WORKFLOW = ".github/workflows/l9-biome-scan.yml"
BIOME_USES = re.compile(
    r"uses:\s*Quantum-L9/l9-ci-sdk/\.github/workflows/l9-biome-scan\.yml@([0-9a-f]{40})"
)


def _load_workflow(path: Path) -> dict:
    # GitHub workflows use the bare key `on:`, which YAML 1.1 parses as the
    # boolean True; PyYAML keeps it as the string "on" only via safe_load when
    # quoted, so read the mapping and tolerate either key.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TypeScriptBiomeConvergenceTests(unittest.TestCase):
    def test_both_files_pin_the_sdk_biome_workflow_to_a_full_sha(self) -> None:
        for path in LINT_TEST_FILES:
            with self.subTest(workflow=path.name):
                match = BIOME_USES.search(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(
                    match,
                    f"{path.name} must invoke the SDK Biome reusable workflow "
                    "pinned to a full 40-char SHA",
                )

    def test_preset_and_starter_pin_the_same_sha(self) -> None:
        shas = {
            BIOME_USES.search(path.read_text(encoding="utf-8")).group(1)
            for path in LINT_TEST_FILES
        }
        self.assertEqual(
            1, len(shas), "preset and starter must pin the same SDK Biome SHA"
        )

    def test_pinned_sha_is_a_supported_sdk_revision(self) -> None:
        # The Biome reusable workflow must come from an SDK revision Core
        # actually allows: the pinned SHA has to match a `supported[]` entry (or
        # the `default` pointer) in the SDK compatibility manifest. This is how
        # "require an SDK revision that exports the reusable Biome workflow" is
        # enforced without editing the pin manifest itself (any edit there trips
        # the heavyweight sdk-pin-mirrors lockstep rule, reserved for real pin
        # changes).
        sha = BIOME_USES.search(PRESET.read_text(encoding="utf-8")).group(1)
        manifest = yaml.safe_load(COMPAT_MANIFEST.read_text(encoding="utf-8"))
        allowed = {
            e["revision"].lower()
            for e in manifest.get("supported", [])
            if isinstance(e, dict) and isinstance(e.get("revision"), str)
        }
        default = manifest.get("default")
        if isinstance(default, dict) and isinstance(default.get("revision"), str):
            allowed.add(default["revision"].lower())
        self.assertIn(
            sha,
            allowed,
            f"pinned Biome SHA {sha} is not an allowed SDK revision in "
            ".l9/sdk-compatibility.yaml",
        )

    def test_biome_job_is_a_reusable_call_not_a_local_reimplementation(self) -> None:
        for path in LINT_TEST_FILES:
            with self.subTest(workflow=path.name):
                jobs = _load_workflow(path)["jobs"]
                self.assertIn("biome", jobs, "expected a `biome` job")
                biome = jobs["biome"]
                self.assertIn(
                    "uses", biome, "biome job must call the SDK reusable workflow"
                )
                # A reusable-workflow caller job must not carry its own steps
                # (that would be copying the SDK implementation into Core).
                self.assertNotIn("steps", biome)

    def test_tsc_typecheck_and_tests_are_preserved(self) -> None:
        for path in LINT_TEST_FILES:
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                jobs = _load_workflow(path)["jobs"]
                self.assertIn("typecheck", jobs, "tsc type-check job removed")
                self.assertIn("test", jobs, "test job removed")
                self.assertIn("tsc --noEmit", text)

    def test_biome_is_sole_formatter_no_eslint_owner(self) -> None:
        # Biome owns JS/TS format+lint; the converged preset must not *invoke*
        # ESLint as a formatter/linter. Inspect parsed job steps (YAML drops
        # comments, so documentation prose mentioning ESLint does not trip this).
        for path in LINT_TEST_FILES:
            with self.subTest(workflow=path.name):
                jobs = _load_workflow(path)["jobs"]
                for job_name, job in jobs.items():
                    for step in job.get("steps", []) or []:
                        blob = " ".join(
                            str(step.get(key, "")) for key in ("name", "run", "uses")
                        ).lower()
                        self.assertNotIn(
                            "eslint",
                            blob,
                            f"{path.name}:{job_name} invokes ESLint; Biome is the "
                            "sole JS/TS formatter/linter owner",
                        )

    def test_lint_test_workflows_are_read_only(self) -> None:
        write_pattern = re.compile(
            r"(?m)^\s+(actions|checks|contents|deployments|discussions|"
            r"id-token|issues|packages|pages|pull-requests|"
            r"repository-projects|security-events|statuses):\s+write"
        )
        for path in LINT_TEST_FILES:
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, re.compile(r"(?m)^\s*contents:\s+read\s*$"))
                self.assertEqual(
                    [], write_pattern.findall(text), f"{path.name} requests write scope"
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

    def test_activation_skill_stamps_and_forbids_hand_authoring(self) -> None:
        skill = ACTIVATION_SKILL.read_text(encoding="utf-8")
        readme = PRESET_README.read_text(encoding="utf-8")
        for text, label in ((skill, "activation skill"), (readme, "preset README")):
            with self.subTest(doc=label):
                self.assertIn("stamp.sh", text)
                self.assertIn("do not hand-author", text.lower())
                self.assertNotIn(
                    "npx eslint",
                    text,
                    f"{label} still routes lint through ESLint",
                )

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
            self.assertEqual((target / "biome.json").read_text(encoding="utf-8"), '{"root": false}\n')


if __name__ == "__main__":
    unittest.main()
