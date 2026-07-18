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


class SDKSourceValidationTests(unittest.TestCase):
    def test_authoritative_source_is_accepted(self) -> None:
        module.validate_inputs(
            "git",
            "https://github.com/Quantum-L9/l9-ci-sdk.git",
            module.EXPECTED_REVISION,
        )

    def test_arbitrary_source_is_rejected(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            module.validate_inputs(
                "shell",
                "https://github.com/Quantum-L9/l9-ci-sdk.git",
                module.EXPECTED_REVISION,
            )

    def test_arbitrary_repository_is_rejected(self) -> None:
        with self.assertRaises(module.ProvisioningError):
            module.validate_inputs(
                "git",
                "https://example.invalid/sdk.git",
                module.EXPECTED_REVISION,
            )


if __name__ == "__main__":
    unittest.main()
