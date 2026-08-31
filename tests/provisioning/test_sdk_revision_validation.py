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

    def test_sdk_v1_revision_is_accepted(self) -> None:
        self.validate("f546f122d33601ea5a4b2592e3482c5c39eddd82")

    def test_prior_default_remains_a_supported_rollback(self) -> None:
        self.validate("0c487747b0fcd172edaefe9e843dac818de8fc12")

    def test_previous_org_pin_remains_a_supported_rollback(self) -> None:
        self.validate("b1a491414ed04bb18d665f8a8755de80947c8200")

    def test_removed_rollback_revision_is_now_rejected(self) -> None:
        # 0779fca… was dropped from the compatibility manifest; an unlisted
        # (even if full 40-hex) revision must fail closed.
        with self.assertRaises(module.ProvisioningError):
            self.validate("0779fca8238011f8abea551895f96584676e9d17")

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
