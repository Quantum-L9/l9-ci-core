"""The release engine's contract declaration in .l9/release-plane.yaml."""

from __future__ import annotations

import pathlib
import sys
import unittest

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from support import ROOT  # noqa: E402

from l9_release.config import REFUSED_REPOSITORIES  # noqa: E402


class ReleasePlaneDeclarationTests(unittest.TestCase):
    def setUp(self) -> None:
        plane = yaml.safe_load((ROOT / ".l9" / "release-plane.yaml").read_text())
        self.declaration = plane["consumer_release_engine"]

    def test_declared_paths_exist(self) -> None:
        for key in ("engine", "runner", "runbook"):
            with self.subTest(key=key):
                self.assertTrue((ROOT / self.declaration[key]).exists())

    def test_core_is_refused_by_contract_and_code(self) -> None:
        self.assertEqual(
            REFUSED_REPOSITORIES,
            {name.lower() for name in self.declaration["refused_repositories"]},
        )
        self.assertIn("quantum-l9/l9-ci-core", REFUSED_REPOSITORIES)

    def test_no_registry_publication(self) -> None:
        self.assertEqual("none", self.declaration["registry_publication"])
        engine = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tools" / "l9_release").glob("*.py")
        )
        for token in ("npm publish", "twine", "uv publish", '"publish"]'):
            with self.subTest(token=token):
                self.assertNotIn(token, engine)


if __name__ == "__main__":
    unittest.main()
