from __future__ import annotations

import re
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
        """The declared version is exact semver; its value is not pinned here.

        ``validate_release.py`` reads the expected release version from
        ``.l9/repo-spec.yaml``, so asserting a literal here would make the
        full suite (which the validator runs) fail on the first version bump.
        """
        text = (ROOT / ".l9/repo-spec.yaml").read_text(encoding="utf-8")
        match = re.search(r"(?m)^\s+version:\s*['\"]?([^'\"\s]+)['\"]?\s*$", text)
        self.assertIsNotNone(match, ".l9/repo-spec.yaml declares no version")
        assert match is not None
        self.assertRegex(
            match.group(1),
            r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$",
        )
        self.assertIn("phase_4:", text)
        self.assertIn("status: implemented", text)


if __name__ == "__main__":
    unittest.main()
