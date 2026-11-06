"""org-ci.yml caches consumer apt package archives around repository verification.

A consumer's ``make setup`` may install OS packages with apt (Website-Bot's
Playwright Chromium fonts, 21.1 MB, took 7-8 minutes on a slow Ubuntu mirror).
These tests keep the cache an optimisation that can never change a verdict:
only downloaded ``.deb`` archives are cached (never installed state), the
restore precedes and the save follows verification, entries are
content-addressed (restore never matches exactly and falls back from this
runner image to its family; the save key is a digest of the archive set that
survives pruning to installed versions), the save runs even after a failed
phase but only when that set changed, both edges are full-SHA pins of one
release, nothing fails open, and the job stays read-only.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORG_CI = ROOT / ".github" / "workflows" / "org-ci.yml"
CACHE_PIN = re.compile(r"^actions/cache/(?P<part>restore|save)@(?P<sha>[0-9a-f]{40})$")


def analyze_steps() -> list[dict[str, Any]]:
    document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = document["jobs"]["analyze"]["steps"]
    return steps


def index_by(steps: list[dict[str, Any]], **match: str) -> int:
    for index, step in enumerate(steps):
        if all(str(step.get(key, "")) == value for key, value in match.items()):
            return index
    raise AssertionError(f"org-ci analyze job has no step matching {match}")


def cache_step(steps: list[dict[str, Any]], part: str) -> int:
    for index, step in enumerate(steps):
        pin = CACHE_PIN.match(str(step.get("uses", "")))
        if pin and pin.group("part") == part:
            return index
    raise AssertionError(f"org-ci analyze job does not use actions/cache/{part}")


class AptArchiveCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.steps = analyze_steps()
        self.route = index_by(self.steps, id="apt_archive_cache_dir")
        self.restore = cache_step(self.steps, "restore")
        self.verify = index_by(self.steps, id="repository_verify")
        self.inventory = index_by(self.steps, id="apt_archive_inventory")
        self.save = cache_step(self.steps, "save")

    def test_cache_wraps_repository_verification(self) -> None:
        self.assertLess(self.route, self.restore)
        self.assertEqual(self.restore + 1, self.verify)
        self.assertEqual(self.verify + 1, self.inventory)
        self.assertEqual(self.inventory + 1, self.save)

    def test_both_cache_edges_pin_one_full_sha_release(self) -> None:
        shas = {
            match.group("sha")
            for match in (
                CACHE_PIN.match(str(self.steps[index]["uses"]))
                for index in (self.restore, self.save)
            )
            if match
        }
        self.assertEqual(1, len(shas), shas)

    def test_only_downloaded_archives_are_cached(self) -> None:
        for index in (self.restore, self.save):
            with self.subTest(step=self.steps[index]["name"]):
                path = str(self.steps[index]["with"]["path"])
                self.assertTrue(path.endswith("/*.deb"), path)
        route = str(self.steps[self.route]["run"])
        self.assertIn('Dir::Cache::Archives \\"${dir}/\\";', route)
        self.assertIn("/etc/apt/apt.conf.d/99-l9-archive-cache", route)

    def test_restore_never_matches_exactly_and_falls_back_image_then_family(
        self,
    ) -> None:
        route = str(self.steps[self.route]["run"])
        self.assertIn(
            'family="l9-apt-archives-v1-${ImageOS:-unknown}-$(uname -m)-"', route
        )
        self.assertIn('image="${family}${ImageVersion:-unknown}-"', route)
        restore = self.steps[self.restore]["with"]
        self.assertEqual(
            "${{ steps.apt_archive_cache_dir.outputs.image }}restore", restore["key"]
        )
        self.assertEqual(
            [
                "${{ steps.apt_archive_cache_dir.outputs.image }}",
                "${{ steps.apt_archive_cache_dir.outputs.family }}",
            ],
            str(restore["restore-keys"]).strip().splitlines(),
        )

    def test_save_key_is_a_digest_of_the_pruned_archive_set(self) -> None:
        inventory = self.steps[self.inventory]
        run = str(inventory["run"])
        self.assertIn("dpkg-query -W -f='${Version}\\n' \"${package}\"", run)
        self.assertIn('sudo rm -f -- "${archive}"', run)
        self.assertIn("sha256sum", run)
        self.assertIn('echo "key=${IMAGE_PREFIX}${digest}"', run)
        self.assertEqual(
            "${{ steps.apt_archive_cache_dir.outputs.image }}",
            inventory["env"]["IMAGE_PREFIX"],
        )
        self.assertEqual(
            "${{ steps.apt_archive_inventory.outputs.key }}",
            self.steps[self.save]["with"]["key"],
        )
        self.assertEqual(
            self.steps[self.restore]["with"]["path"],
            self.steps[self.save]["with"]["path"],
        )

    def test_save_runs_after_failed_phases_only_when_the_set_changed(self) -> None:
        inventory = str(self.steps[self.inventory]["if"])
        self.assertTrue(inventory.startswith("always()"), inventory)
        save = str(self.steps[self.save]["if"])
        self.assertTrue(save.startswith("always()"), save)
        self.assertIn("steps.apt_archive_inventory.outputs.count != '0'", save)
        self.assertIn(
            "steps.apt_archive_inventory.outputs.key != steps.apt_archive_cache.outputs.cache-matched-key",
            save,
        )

    def test_cache_never_fails_open_and_job_stays_read_only(self) -> None:
        for index in range(self.route, self.save + 1):
            with self.subTest(step=self.steps[index].get("name")):
                self.assertNotIn("continue-on-error", self.steps[index])
        document = yaml.safe_load(ORG_CI.read_text(encoding="utf-8"))
        self.assertEqual(
            {"contents": "read"}, document["jobs"]["analyze"]["permissions"]
        )


if __name__ == "__main__":
    unittest.main()
