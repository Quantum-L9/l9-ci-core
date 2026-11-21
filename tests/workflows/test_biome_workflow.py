"""biome.yml: Core's Biome check as its own reusable job (operator, 2026-11-21).

Step one of moving Biome out of org-ci.yml's ``analyze`` monolith: the
workflow lands on main first, so org-ci.yml can then call it at a pinned Core
revision (step two) and enforce its result in the required job, as it does the
security job's. These tests run the workflow's own configuration step against
real directories, check its wiring and pins, and prove the promotion canary
can inline it as a sibling job.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
BIOME = WORKFLOWS / "biome.yml"
ORG_CI = WORKFLOWS / "org-ci.yml"
CORE = "Quantum-L9/l9-ci-core/.github/actions/"

sys.path.insert(0, str(ROOT / "tools"))

import promotion_canary as pc  # noqa: E402


def document() -> dict[Any, Any]:
    return yaml.safe_load(BIOME.read_text(encoding="utf-8"))


def steps() -> list[dict[str, Any]]:
    return document()["jobs"]["biome"]["steps"]


def step(identifier: str) -> dict[str, Any]:
    [found] = [s for s in steps() if s.get("id") == identifier]
    return found


class ConfigurationStepTests(unittest.TestCase):
    def run_step(self, files: tuple[str, ...]) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in files:
                (root / name).write_text("{}\n", encoding="utf-8")
            output = root / "output"
            summary = root / "summary"
            output.write_text("", encoding="utf-8")
            summary.write_text("", encoding="utf-8")
            subprocess.run(
                ["bash", "-c", step("config")["run"]],
                cwd=root,
                env={
                    "PATH": os.environ["PATH"],
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
                check=True,
                capture_output=True,
            )
            return output.read_text(), summary.read_text()

    def test_no_configuration_is_not_applicable_and_says_so(self) -> None:
        output, summary = self.run_step(())
        self.assertIn("applies=false", output)
        self.assertIn("`not_applicable`", summary)

    def test_either_configuration_file_applies(self) -> None:
        for name in ("biome.json", "biome.jsonc"):
            with self.subTest(file=name):
                output, summary = self.run_step((name,))
                self.assertIn("applies=true", output)
                self.assertEqual("", summary)


class WiringTests(unittest.TestCase):
    def test_reusable_only_read_only_and_blocking_by_default(self) -> None:
        triggers = document()[True]
        self.assertEqual(["workflow_call"], list(triggers))
        self.assertEqual(
            "blocking", triggers["workflow_call"]["inputs"]["mode"]["default"]
        )
        self.assertEqual({"contents": "read"}, document()["permissions"])
        self.assertEqual(
            {"contents": "read"}, document()["jobs"]["biome"]["permissions"]
        )

    def test_nothing_is_installed_without_a_configuration(self) -> None:
        for identifier in ("install", "check"):
            with self.subTest(step=identifier):
                self.assertEqual(
                    "steps.config.outputs.applies == 'true'", step(identifier)["if"]
                )
        names = [s.get("id") for s in steps()]
        self.assertLess(names.index("config"), names.index("install"))
        self.assertEqual(names.index("install") + 1, names.index("check"))

    def test_the_check_runs_core_actions_at_the_fleet_pin(self) -> None:
        pins = {
            str(s["uses"]).split("@")[1]
            for s in steps()
            if str(s.get("uses", "")).startswith(CORE)
        }
        org_ci = ORG_CI.read_text(encoding="utf-8")
        self.assertEqual(1, len(pins))
        (pin,) = pins
        self.assertIn(f"install-biome@{pin}", org_ci)
        self.assertTrue(
            str(step("install")["uses"]).startswith(f"{CORE}install-biome@")
        )
        self.assertTrue(
            str(step("check")["uses"]).startswith(f"{CORE}run-biome-check@")
        )
        self.assertEqual("${{ inputs.mode }}", step("check")["with"]["mode"])
        self.assertEqual(
            "${{ steps.install.outputs.executable }}",
            step("check")["with"]["executable"],
        )

    def test_findings_are_annotated_at_file_and_line(self) -> None:
        """Signal law: Biome's own GitHub reporter names each diagnostic."""
        checker = (
            ROOT / ".github" / "actions" / "run-biome-check" / "check_biome.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"--reporter=github"', checker)

    def test_the_promotion_canary_can_inline_it_as_a_sibling_job(self) -> None:
        body = pc.inline_job(
            "biome", BIOME.read_text(encoding="utf-8"), {}, "biome.yml"
        )
        self.assertIn("id: biome__config", body)
        # One id per step: an id the inliner misses would be duplicated.
        self.assertEqual(len(steps()), body.count("        id: "))
        self.assertIn("steps.biome__config.outputs.applies", body)
        self.assertIn("repository: ${{ matrix.repository }}", body)
        self.assertNotIn("inputs.", body)


if __name__ == "__main__":
    unittest.main()
