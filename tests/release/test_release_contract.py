from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ReleaseContractTests(unittest.TestCase):
    def test_phase_4_contracts_exist(self) -> None:
        expected = (
            ROOT / ".l9/publication-contract.yaml",
            ROOT / ".github/workflows/publish-analysis.yml",
            ROOT / ".github/workflows/release-validation.yml",
            ROOT / ".github/actions/render-publication/action.yml",
            ROOT / ".github/actions/publish-check/action.yml",
            ROOT / ".github/actions/validate-release/action.yml",
        )
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_release_version_is_declared(self) -> None:
        text = (ROOT / ".l9/repo-spec.yaml").read_text(encoding="utf-8")
        self.assertIn("version: 2.0.0", text)
        self.assertIn("phase_4:", text)
        self.assertIn("status: implemented", text)


if __name__ == "__main__":
    unittest.main()
