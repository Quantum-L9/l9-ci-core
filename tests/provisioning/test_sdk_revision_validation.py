from __future__ import annotations
import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "actions"
    / "provision-sdk"
    / "provision.py"
)
spec = importlib.util.spec_from_file_location("provision_sdk", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SDKRevisionValidationTests(unittest.TestCase):
    def validate(self, revision: str) -> None:
        module.validate_inputs(
            "git",
            "https://github.com/Quantum-L9/l9-ci-sdk.git",
            revision,
        )

    def test_exact_revision_is_accepted(self) -> None:
        self.validate(module.EXPECTED_REVISION)

    def test_short_revision_is_rejected(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            self.validate("c78486e")

    def test_branch_is_rejected(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            self.validate("main")

    def test_tag_is_rejected(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            self.validate("v2.0.0")

    def test_unlisted_full_revision_is_rejected(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            self.validate("0" * 40)


if __name__ == "__main__":
    unittest.main()
