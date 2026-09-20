"""Dependabot MANIFEST reseal workflow stays actor-gated and SHA-pinned."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "manifest-reseal.yml"


class ManifestResealWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_only_for_dependabot(self) -> None:
        self.assertIn("pull_request:", self.text)
        self.assertIn("github.actor == 'dependabot[bot]'", self.text)
        self.assertNotIn("pull_request_target:", self.text)
        self.assertNotIn("secrets: inherit", self.text)

    def test_checkout_is_sha_pinned_to_the_pr_head(self) -> None:
        self.assertRegex(
            self.text,
            r"uses:\s*actions/checkout@[0-9a-f]{40}",
        )
        self.assertIn("ref: ${{ github.head_ref || github.ref_name }}", self.text)

    def test_reseals_through_the_production_command(self) -> None:
        self.assertIn("python3 -m tools.l9_repo reseal-manifest", self.text)
        self.assertIn("git add -- MANIFEST.sha256", self.text)
        self.assertIn("git diff --cached --quiet -- MANIFEST.sha256", self.text)
        self.assertIn('git push origin "HEAD:${PR_HEAD}"', self.text)
        self.assertNotIn("--no-verify", self.text)
        self.assertNotIn("git push -f", self.text)
        self.assertNotIn("git push --force", self.text)


if __name__ == "__main__":
    unittest.main()
